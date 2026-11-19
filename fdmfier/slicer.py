# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Slice evaluated triangles in millimeters and produce editable extrusion paths."""

from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass
from math import ceil, cos, floor, hypot, pi, sin, sqrt, tan

import pyclipper as clip

SCALE = 10000
MAX_LAYERS = 2000
MAX_POINTS = 750_000
MAX_INTERSECTIONS = 2_000_000
MAX_SUPPORT_SAMPLES = 350_000
WALL, INFILL, SUPPORT, SKIRT, WEB, BRIM = range(6)


class SliceError(ValueError):
    pass


@dataclass
class Path:
    points: list
    layer: int
    feature: int
    closed: bool = False
    wall: bool = False
    support_below: list | None = None
    activation: float = 0.0
    top_skin: bool = False


def units(mm):
    return round(mm * SCALE)


def polygon_op(subject, other=(), operation=clip.CT_UNION, fill=clip.PFT_NONZERO):
    if not subject:
        return []
    if not other and operation == clip.CT_INTERSECTION:
        return []
    engine = clip.Pyclipper()
    engine.AddPaths(subject, clip.PT_SUBJECT, True)
    if other:
        engine.AddPaths(other, clip.PT_CLIP, True)
    return engine.Execute(operation, fill, fill)


def offset(polygons, distance):
    if not polygons or not distance:
        return polygons
    engine = clip.PyclipperOffset(2, 25)
    engine.AddPaths(polygons, clip.JT_MITER, clip.ET_CLOSEDPOLYGON)
    return engine.Execute(distance)


def bounds(paths):
    points = [point for path in paths for point in path]
    return (
        min(x for x, _ in points), min(y for _, y in points),
        max(x for x, _ in points), max(y for _, y in points),
    )


def stitch(segments, coordinates, layer_number):
    neighbors = defaultdict(list)
    for a, b in segments:
        neighbors[a].append(b)
        neighbors[b].append(a)
    if any(len(edges) != 2 for edges in neighbors.values()):
        raise SliceError(f"Layer {layer_number} has an open contour in the evaluated mesh. Try Repair / Remesh Copy.")
    loops = []
    while neighbors:
        start = min(neighbors)
        previous, current = start, neighbors.pop(start)[0]
        loop = [start]
        while current != start:
            loop.append(current)
            a, b = neighbors.pop(current)
            previous, current = current, b if a == previous else a
        # Trace the mesh topology before rounding into Clipper coordinates.
        # Distinct edges can land on the same integer point near a vertex.
        polygon = [coordinates[edge] for edge in loop]
        if len(set(polygon)) >= 3:
            loops.append(polygon)
    return polygon_op(loops, fill=clip.PFT_EVENODD)


def check_cancel(cancelled):
    if cancelled():
        raise SliceError("Print preview generation cancelled.")


def section_steps(vertices, triangles, height):
    top = max(vertex[2] for vertex in vertices)
    count = ceil(top / height - 1e-9)
    if count < 1 or count > MAX_LAYERS:
        raise SliceError(f"Preview requires 1–{MAX_LAYERS} layers. Increase Layer Height or reduce Target Size.")
    levels = [(layer * height + min((layer + 1) * height, top)) / 2 for layer in range(count)]
    # Nudge planes through vertices toward the upper side. Intersections use a
    # half-open edge convention to handle horizontal faces consistently.
    levels = [level + 1e-8 for level in levels]
    segments = [[] for _ in levels]
    coordinates = [{} for _ in levels]
    intersections = 0
    for number, triangle in enumerate(triangles):
        if number % 128 == 0:
            yield 0.05 + 0.19 * number / len(triangles), "Slicing mesh"
        low, middle, high = sorted(triangle, key=lambda vertex: vertices[vertex][2])
        first = bisect_left(levels, vertices[low][2])
        last = bisect_left(levels, vertices[high][2])
        intersections += last - first
        if intersections > MAX_INTERSECTIONS:
            raise SliceError(f"Slicing exceeded {MAX_INTERSECTIONS:,} triangle-plane intersections. Increase Layer Height or use a lower-resolution remesh.")
        for index in range(first, last):
            if index > first and index % 256 == 0:
                yield 0.05 + 0.19 * number / len(triangles), "Slicing mesh"
            z = levels[index]
            edge = (middle, high) if z >= vertices[middle][2] else (low, middle)
            pair = []
            points = coordinates[index]
            for a, b in ((low, high), edge):
                key = (min(a, b), max(a, b))
                if key not in points:
                    start, end = vertices[a], vertices[b]
                    t = (z - start[2]) / (end[2] - start[2])
                    points[key] = tuple(units(start[axis] + (end[axis] - start[axis]) * t)
                                        for axis in (0, 1))
                pair.append(key)
            segments[index].append(tuple(pair))
    result = []
    for index, layer in enumerate(segments):
        yield 0.24 + 0.11 * index / count, "Joining layer contours"
        result.append(stitch(layer, coordinates[index], index + 1))
    return result


def hatch(polygons, spacing, angle, phase=0):
    if not polygons:
        return []
    ux, uy = cos(angle), sin(angle)
    nx, ny = -uy, ux
    x0, y0, x1, y1 = bounds(polygons)
    corners = [(x, y) for x in (x0, x1) for y in (y0, y1)]
    along = [x * ux + y * uy for x, y in corners]
    across = [x * nx + y * ny for x, y in corners]
    lo, hi = min(along) - 2, max(along) + 2
    phase %= spacing
    first, last = floor((min(across) - phase) / spacing), ceil((max(across) - phase) / spacing)
    if last - first > MAX_POINTS // 2:
        raise SliceError("Infill path budget exceeded. Increase Extrusion Width or reduce Target Size.")
    lines = [
        [(round(x * ux + (i * spacing + phase) * nx),
          round(x * uy + (i * spacing + phase) * ny)) for x in (lo, hi)]
        for i in range(first, last + 1)
    ]
    return clip_lines(lines, polygons)


def clip_lines(lines, polygons):
    if not lines or not polygons:
        return []
    engine = clip.Pyclipper()
    engine.AddPaths(lines, clip.PT_SUBJECT, False)
    engine.AddPaths(polygons, clip.PT_CLIP, True)
    result = engine.Execute2(clip.CT_INTERSECTION, clip.PFT_NONZERO, clip.PFT_NONZERO)
    return sorted(clip.OpenPathsFromPolyTree(result))


def concentric(polygons, spacing):
    while polygons:
        yield from polygons
        polygons = offset(polygons, -spacing)


def honeycomb(polygons, width, density):
    if not polygons:
        return []
    # Shared hex edges have line length per unit area 2 / (sqrt(3) * side).
    side = max(width * 1.5, 2 * width / (3 ** 0.5 * density))
    stride_y = 3 ** 0.5 * side
    x0, y0, x1, y1 = bounds(polygons)
    columns = range(floor(x0 / (1.5 * side)) - 1, ceil(x1 / (1.5 * side)) + 2)
    rows = range(floor(y0 / stride_y) - 1, ceil(y1 / stride_y) + 2)
    if len(columns) * len(rows) > MAX_POINTS // 6:
        raise SliceError("Honeycomb path budget exceeded. Reduce infill or increase Extrusion Width.")
    edges = set()
    for col in columns:
        for row in rows:
            x, y = col * 1.5 * side, (row + (col % 2) / 2) * stride_y
            corners = [(round(x + side * cos(i * pi / 3)), round(y + side * sin(i * pi / 3)))
                       for i in range(6)]
            edges.update(tuple(sorted((a, b))) for a, b in zip(corners, corners[1:] + corners[:1]))
    return clip_lines(sorted(edges), polygons)


def fill_paths(region, pattern, density, width, index, height=0, adaptive=None, pattern_bounds=None):
    if not region or density <= 0:
        return
    if pattern == "CONCENTRIC":
        for line in concentric(region, max(1, round(width / density))):
            yield line, True
        return
    if pattern == "HONEYCOMB":
        for line in honeycomb(region, width, density):
            yield line, False
        return
    if pattern == "CUBIC":
        for angle in (pi / 2, 7 * pi / 6, 11 * pi / 6):
            for line in hatch(region, 3 * width / density, angle, (index + 0.5) * height / sqrt(2)):
                yield line, False
        return
    if pattern in {"GYROID", "CROSS_3D", "ADAPTIVE_CUBIC", "HILBERT", "ARCHIMEDEAN", "OCTAGRAM"}:
        from . import patterns

        area = pattern_bounds or bounds(region)
        z = (index + 0.5) * height
        if pattern == "GYROID":
            lines = patterns.gyroid(area, width, density, z)
        elif pattern == "CROSS_3D":
            lines = patterns.cross_3d(area, width, density, z)
        elif pattern == "ADAPTIVE_CUBIC":
            lines = adaptive.lines(z) if adaptive is not None else ()
        elif pattern == "HILBERT":
            lines = patterns.hilbert(area, width)
        else:
            lines = patterns.spiral(area, width, octagram=pattern == "OCTAGRAM")
        for line in clip_lines(lines, region):
            closed = len(line) > 3 and line[0] == line[-1]
            yield line[:-1] if closed else line, closed
        return
    angles = {
        "LINES": [pi / 4 + (index % 2) * pi / 2],
        "PARALLEL": [pi / 4],
        "GRID": [pi / 4, 3 * pi / 4],
        "TRIANGLES": [0, pi / 3, 2 * pi / 3],
    }[pattern]
    for angle in angles:
        for line in hatch(region, width * len(angles) / density, angle):
            yield line, False


def skin_region(core, layers, index, depth, direction):
    enclosed = core
    for distance in range(1, depth + 1):
        other = index + direction * distance
        if not 0 <= other < len(layers):
            return core
        enclosed = polygon_op(enclosed, layers[other], clip.CT_INTERSECTION)
        if not enclosed:
            return core
    return polygon_op(core, enclosed, clip.CT_DIFFERENCE)


def inside(point, polygons):
    winding = 0
    for polygon in polygons:
        location = clip.PointInPolygon(point, polygon)
        if location == -1:
            return True
        if location:
            winding += 1 if clip.Orientation(polygon) else -1
    return winding != 0


def support_steps(layers, width, height, tree, angle, flat_faces, *, build_plate_only=False, tip_spacing=0):
    """Route sampled overhangs downward, anchoring routes to the bed or lower shelves."""
    natural_spacing = max(width * 6, units(2))
    spacing = max(natural_spacing, tip_spacing)
    radius = max(width, natural_spacing // 5)
    clearance = radius + width // 2
    allowance = round(height * tan(angle * pi / 180))
    forbidden = []
    count = len(layers)
    for index, layer in enumerate(layers):
        yield 0.74 + 0.03 * index / count, "Checking support clearance"
        forbidden.append(offset(layer, clearance))
    tips = defaultdict(set)
    samples = 0
    for index in range(2, count):
        update = 0.77 + 0.05 * index / count, "Finding overhangs"
        yield update
        overhang = polygon_op(layers[index], offset(layers[index - 1], allowance), clip.CT_DIFFERENCE)
        overhang = polygon_op(overhang + flat_faces.get(index, []))
        overhang = polygon_op(overhang, forbidden[index - 2], clip.CT_DIFFERENCE)
        # Sample each solid island independently so small ledges also receive tips.
        for polygon in overhang:
            if not clip.Orientation(polygon):
                continue
            x0, y0, x1, y1 = bounds([polygon])
            xs = range(ceil(x0 / spacing) * spacing, x1 + 1, spacing)
            ys = range(ceil(y0 / spacing) * spacing, y1 + 1, spacing)
            samples += len(xs) * len(ys)
            if samples > MAX_SUPPORT_SAMPLES:
                raise SliceError(f"Support sampling exceeded {MAX_SUPPORT_SAMPLES:,} candidates at layer {index + 1}. Increase Support Spacing or Layer Height.")
            candidates = []
            for number, point in enumerate((x, y) for x in xs for y in ys):
                if number and number % 256 == 0:
                    yield update
                if inside(point, [polygon]) and inside(point, overhang):
                    candidates.append(point)
            if not candidates:
                # An interior hatch segment supplies a sample for narrow islands.
                local = polygon_op(overhang, [polygon], clip.CT_INTERSECTION)
                lines = hatch(local, max(width // 2, 1), 0)
                if lines:
                    line = max(lines, key=lambda line: abs(line[-1][0] - line[0][0]))
                    a, b = line[0], line[-1]
                    candidates = [((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)]
            for number, point in enumerate(candidates):
                if number and number % 256 == 0:
                    yield update
                if inside(point, overhang) and not inside(point, forbidden[index - 2]):
                    tips[index - 2].add(point)

    samples = 0
    routes = [{} for _ in layers]
    active = {}
    incoming_radii = {}
    for index in range(count - 1, -1, -1):
        update = 0.82 + 0.06 * (count - 1 - index) / count, "Routing supports"
        yield update
        for point in tips[index]:
            active[point] = active.get(point, 0) + 1
        samples += len(active)
        if samples > MAX_SUPPORT_SAMPLES:
            raise SliceError(f"Support routing exceeded {MAX_SUPPORT_SAMPLES:,} branch-layer steps. Increase Support Spacing or Layer Height.")
        parents = {}
        parent_radii = {}
        step = min(height, width // 2)
        for number, (point, load) in enumerate(sorted(active.items())):
            if number and number % 128 == 0:
                yield update
            branch_radius = radius
            if tree and point in incoming_radii:
                previous = incoming_radii[point]
                target = min(spacing * 1.5, radius * (0.45 + sqrt(load)))
                # Use the widest incoming branch as the starting radius.
                # A new merge changes the target, while growth stays limited
                # by the vertical distance traveled on this layer.
                growth = min(height * 0.18, (target - previous) * height / spacing)
                branch_radius = previous + max(0, growth)
            x, y = point
            parent = point
            if index:
                blocked = forbidden[index - 1]
                if tree:
                    # Nearby tips converge toward the same grid anchor at one step per layer.
                    grid = spacing * 2
                    dx = round(x / grid) * grid - x
                    dy = round(y / grid) * grid - y
                    distance = hypot(dx, dy)
                    scale = min(1, step / distance) if distance else 0
                    preferred = (x + round(dx * scale), y + round(dy * scale))
                    alternatives = [(x + step, y), (x - step, y), (x, y + step), (x, y - step)]
                    options = [preferred, point, *alternatives]
                else:
                    options = [point]
                parent = next((p for p in options if not inside(p, blocked)
                               and not inside(p, forbidden[index])
                               and not inside(((p[0] + x) // 2, (p[1] + y) // 2), blocked)), None)
                if parent is None:
                    # A lower shelf can carry supports when the bed is obstructed.
                    if build_plate_only or index < 2 or not inside(point, layers[index - 2]):
                        continue
                else:
                    parents[parent] = parents.get(parent, 0) + load
                    if tree:
                        parent_radii[parent] = max(parent_radii.get(parent, 0), branch_radius)
            routes[index][point] = (parent, round(branch_radius))
        active = parents
        incoming_radii = parent_radii

    rooted = set()
    for index, layer in enumerate(routes):
        update = 0.88 + 0.06 * index / count, "Building support paths"
        yield update
        next_rooted = set()
        circles = []
        for number, (point, (parent, r)) in enumerate(layer.items()):
            if number and number % 128 == 0:
                yield update
            if index and parent is not None and parent not in rooted:
                continue
            next_rooted.add(point)
            circles.append([
                (point[0] + round(r * cos(i * pi / 6)), point[1] + round(r * sin(i * pi / 6)))
                for i in range(12)
            ])
        rooted = next_rooted
        area = polygon_op(polygon_op(circles), offset(layers[index], width // 2), clip.CT_DIFFERENCE)
        for contour in area:
            yield Path(contour, index, SUPPORT, True)
        for line in hatch(offset(area, -width), width * 2, (index % 2) * pi / 2):
            yield Path(line, index, SUPPORT)


def web_paths(layer, index, width):
    """Find short air gaps between solid regions along likely travel directions."""
    angle = (index % 4) * pi / 4
    ux, uy = cos(angle), sin(angle)
    nx, ny = -uy, ux
    spacing = max(width * 9, units(3))
    rows = defaultdict(list)
    for line in hatch(layer, spacing, angle):
        ends = sorted((line[0], line[-1]), key=lambda p: p[0] * ux + p[1] * uy)
        x, y = ends[0]
        rows[round((x * nx + y * ny) / spacing)].append(ends)
    candidates = []
    for intervals in rows.values():
        intervals.sort(key=lambda ends: ends[0][0] * ux + ends[0][1] * uy)
        for (a0, a), (b, b1) in zip(intervals, intervals[1:]):
            length = hypot(b[0] - a[0], b[1] - a[1])
            if not width * 1.5 <= length <= min(units(14), width * 30):
                continue
            overlap = width * 0.6
            if min(hypot(a[0] - a0[0], a[1] - a0[1]),
                   hypot(b1[0] - b[0], b1[1] - b[1])) < 2 * overlap:
                continue
            start = (round(a[0] - ux * overlap), round(a[1] - uy * overlap))
            end = (round(b[0] + ux * overlap), round(b[1] + uy * overlap))
            candidates.append((start, end))
    if not candidates:
        return
    # Keep stringing sparse and make successive layers choose different gaps.
    for slot in range(min(2, len(candidates))):
        start, end = candidates[(index * 7 + slot) % len(candidates)]
        points = [tuple(round(a + (b - a) * t / 8) for a, b in zip(start, end))
                  for t in range(9)]
        threshold = 0.12 + ((index * 37 + slot * 53) % 79) / 100
        yield Path(points, index, WEB, activation=threshold)


def flat_overhang_steps(vertices, triangles, layers, layer_height):
    """Include horizontal undersides even when the slope allowance covers them."""
    from bisect import bisect_right

    top = max(point[2] for point in vertices)
    midpoints = [(i * layer_height + min((i + 1) * layer_height, top)) / 2 + 1e-8
                 for i in range(len(layers))]
    faces = defaultdict(list)
    for number, triangle in enumerate(triangles):
        if number % 128 == 0:
            yield 0.73 + 0.009 * number / len(triangles), "Checking horizontal overhangs"
        a, b, c = (vertices[i] for i in triangle)
        if max(a[2], b[2], c[2]) - min(a[2], b[2], c[2]) > 1e-6:
            continue
        index = bisect_right(midpoints, a[2])
        if not 2 <= index < len(layers):
            continue
        polygon = [(units(x), units(y)) for x, y, _ in (a, b, c)]
        if clip.Area(polygon):
            faces[index].append(polygon if clip.Orientation(polygon) else polygon[::-1])
    # Slice occupancy distinguishes undersides from tops without relying on
    # mesh winding, including for objects with mirrored transforms.
    result = {}
    for i, polygons in faces.items():
        yield 0.739, "Checking horizontal overhangs"
        result[i] = polygon_op(polygon_op(polygons, layers[i], clip.CT_INTERSECTION),
                               layers[i - 1], clip.CT_DIFFERENCE)
    return result


def simplify_wall(contour, tolerance):
    """Reduce a closed contour with a bound on deviation from the input."""
    start = min(range(len(contour)), key=contour.__getitem__)
    points = contour[start:] + contour[:start]
    x, y = points[0]
    split = max(range(1, len(points)), key=lambda i: (points[i][0] - x)**2 + (points[i][1] - y)**2)
    points.append(points[0])
    keep = {0, split}
    pending = [(0, split), (split, len(points) - 1)]
    tolerance_squared = tolerance * tolerance
    while pending:
        first, last = pending.pop()
        x, y = points[first]
        dx, dy = points[last][0] - x, points[last][1] - y
        length_squared = dx * dx + dy * dy
        worst, cut = tolerance_squared, None
        for i in range(first + 1, last):
            px, py = points[i][0] - x, points[i][1] - y
            t = max(0, min(1, (px * dx + py * dy) / length_squared)) if length_squared else 0
            deviation = (px - t * dx)**2 + (py - t * dy)**2
            if deviation > worst:
                worst, cut = deviation, i
        if cut is not None:
            keep.add(cut)
            pending.extend(((first, cut), (cut, last)))
    result = [points[i] for i in sorted(keep)]
    if len(result) >= 3 and clip.Area(result) * clip.Area(contour) > 0:
        return result
    return contour


def generate_steps(vertices, triangles, settings):
    """Yield progress between slicing steps and return the finished paths."""
    layers = yield from section_steps(vertices, triangles, settings.layer_height)
    width = max(1, units(settings.width))
    height = units(settings.layer_height)
    paths = []
    point_count = 0
    narrow = 0
    pattern_bounds = (
        units(min(vertex[0] for vertex in vertices)), units(min(vertex[1] for vertex in vertices)),
        units(max(vertex[0] for vertex in vertices)), units(max(vertex[1] for vertex in vertices)),
    )
    adaptive = None
    if settings.pattern == "ADAPTIVE_CUBIC" and 0 < settings.density < 100:
        from .patterns import AdaptiveCubic

        yield 0.35, "Building adaptive cubic cells"
        adaptive = AdaptiveCubic(vertices, triangles, width, settings.density / 100)
        yield from adaptive.build_steps()

    def add(path):
        nonlocal point_count
        # Closed paths are resampled for localized seam and imperfection control.
        points = path.points
        if path.wall:
            pairs = zip(points, points[1:] + points[:1])
            point_count += sum(max(1, ceil(hypot(b[0] - a[0], b[1] - a[1]) / (2 * width))) for a, b in pairs)
        else:
            point_count += len(points)
        if point_count > MAX_POINTS:
            adjustment = "Increase Layer Height or Extrusion Width."
            if path.feature == SUPPORT:
                adjustment = "Increase Support Spacing or Layer Height."
            raise SliceError(f"Preview exceeded {MAX_POINTS:,} path vertices at layer {path.layer + 1}. {adjustment}")
        paths.append(path)

    path_share = 0.38 if settings.supports != "OFF" else 0.59
    for index, layer in enumerate(layers):
        yield 0.35 + path_share * index / len(layers), "Building walls and infill"
        if not layer:
            continue
        if index and index % 3 == 1:
            for path in web_paths(layer, index, width):
                add(path)
        lower = offset(layers[index - 1], round(width * 0.45)) if index else None
        for wall in range(settings.walls):
            contours = offset(layer, -round((wall + 0.5) * width))
            if not contours:
                narrow += wall == 0
                break
            if wall == 0:
                for island in layer:
                    if clip.Orientation(island) and not offset([island], -width // 2):
                        narrow += 1
            for contour in contours:
                # Bound simplification error so it cannot shave a curved
                # contour differently on each slice of a triangulated wall.
                contour = simplify_wall(contour, max(1, width * 0.008))
                add(Path(contour, index, WALL, True, True, lower if wall == 0 else None))
        core = offset(layer, -(settings.walls * width + width // 2))
        if not core:
            continue
        top = skin_region(core, layers, index, settings.skins, 1)
        bottom = skin_region(core, layers, index, settings.bottom_layers, -1)
        # On thin roofs, give the outward face its top pattern even when
        # the requested top and bottom skin depths overlap.
        if index == len(layers) - 1:
            bottom = polygon_op(bottom, top, clip.CT_DIFFERENCE)
        else:
            top = polygon_op(top, bottom, clip.CT_DIFFERENCE)
        sparse = polygon_op(polygon_op(core, bottom, clip.CT_DIFFERENCE), top, clip.CT_DIFFERENCE)
        density = settings.density / 100
        for region, pattern, fill, top_skin in (
            (bottom, settings.bottom_pattern, 1, False),
            (top, settings.top_pattern, 1, True),
            (sparse, "LINES" if density == 1 else settings.pattern, density, False),
        ):
            for line, closed in fill_paths(region, pattern, fill, width, index, height,
                                           adaptive, pattern_bounds):
                add(Path(line, index, INFILL, closed, top_skin=top_skin))

    if settings.supports != "OFF":
        flats = yield from flat_overhang_steps(vertices, triangles, layers, settings.layer_height)
        for item in support_steps(layers, width, height, settings.supports == "TREE",
                                  min(89.9, getattr(settings, "support_angle", 45)),
                                  flats, build_plate_only=settings.build_plate_only,
                                  tip_spacing=units(settings.support_spacing)):
            if isinstance(item, Path):
                add(item)
            else:
                yield item
    yield 0.94, "Finishing extrusion paths"
    brim_extent = 0
    if settings.brim and layers[0]:
        # Start just inside tangency with the first perimeter and slightly
        # overlap adjacent tracks so the brim reads as one attached strip.
        first = width * 0.46
        spacing = width * 0.96
        loops = max(1, ceil((units(settings.brim_width) - first - width / 2) / spacing) + 1)
        for loop in range(loops):
            yield 0.94, "Building brim"
            distance = round(first + loop * spacing)
            for outline in offset(layers[0], distance):
                if clip.Orientation(outline):
                    add(Path(outline, 0, BRIM, True))
            brim_extent = distance + width / 2
    if settings.skirt and layers[0]:
        for loop in range(settings.skirt_loops):
            yield 0.94, "Building skirt"
            distance = round(brim_extent + units(settings.skirt_offset) + (loop + 0.5) * width)
            for outline in offset(layers[0], distance):
                if clip.Orientation(outline):
                    add(Path(outline, 0, SKIRT, True))
    if not any(path.feature == WALL for path in paths):
        raise SliceError("No walls fit this mesh. Reduce Extrusion Width or increase Target Size.")
    return paths, len(layers), narrow


def generate(vertices, triangles, settings, cancelled=lambda: False, progress=lambda factor, label: None):
    """Run the same slicing steps synchronously for scripts and background Blender."""
    steps = generate_steps(vertices, triangles, settings)
    try:
        while True:
            check_cancel(cancelled)
            try:
                update = next(steps)
            except StopIteration as complete:
                return complete.value
            progress(*update)
    finally:
        steps.close()
