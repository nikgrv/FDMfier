# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Layer intersections and surface curves in integer slicing coordinates."""

from collections import defaultdict
from math import ceil, cos, floor, hypot, log2, pi, sin, sqrt

import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .slicer import MAX_POINTS, SCALE, SliceError


def gyroid(area, width, density, z):
    # Intersect sin(x)cos(y) + sin(y)cos(z) + sin(z)cos(x) = 0.
    # Average intersection length is approximately 2.5 times the period per cell.
    period = 2.5 * width / density
    step = max(width / 2, period / 48)
    x0, y0, x1, y1 = area
    left, bottom = floor(x0 / step), floor(y0 / step)
    columns = ceil(x1 / step) - left + 1
    rows = ceil(y1 / step) - bottom + 1
    if rows * columns > MAX_POINTS * 2:
        raise SliceError("Gyroid sampling exceeds the path budget. Reduce infill or increase Extrusion Width.")
    frequency = 2 * pi / period
    xs = (np.arange(columns) + left) * step
    ys = (np.arange(rows) + bottom) * step
    # Avoid exact saddle vertices at special layer heights.
    phase = z * frequency + 1e-8
    field = (np.sin(xs * frequency)[None, :] * np.cos(ys * frequency)[:, None]
             + np.sin(ys * frequency)[:, None] * cos(phase)
             + sin(phase) * np.cos(xs * frequency)[None, :])
    cases = ((field[:-1, :-1] >= 0).astype(np.uint8)
             + 2 * (field[:-1, 1:] >= 0)
             + 4 * (field[1:, 1:] >= 0)
             + 8 * (field[1:, :-1] >= 0))
    active_rows, active_columns = np.nonzero((cases != 0) & (cases != 15))
    if len(active_rows) > MAX_POINTS:
        raise SliceError("Gyroid contours exceed the path budget. Reduce infill or increase Extrusion Width.")
    coordinates, neighbors = {}, defaultdict(list)
    pairs = {
        1: ((3, 0),), 2: ((0, 1),), 3: ((3, 1),), 4: ((1, 2),),
        6: ((0, 2),), 7: ((3, 2),), 8: ((2, 3),), 9: ((0, 2),),
        11: ((1, 2),), 12: ((1, 3),), 13: ((0, 1),), 14: ((3, 0),),
    }
    for row, col in zip(active_rows.tolist(), active_columns.tolist()):
        values = (field[row, col], field[row, col + 1],
                  field[row + 1, col + 1], field[row + 1, col])
        case = int(cases[row, col])
        if case in (5, 10):
            # The determinant selects connectivity at a bilinear saddle.
            edges = (((0, 1), (2, 3)) if values[0] * values[2] > values[1] * values[3]
                     else ((3, 0), (1, 2)))
        else:
            edges = pairs[case]
        keys = (("h", row, col), ("v", row, col + 1),
                ("h", row + 1, col), ("v", row, col))
        for first, second in edges:
            for edge in (first, second):
                key = keys[edge]
                if key in coordinates:
                    continue
                direction, r, c = key
                a = field[r, c]
                b = field[r, c + 1] if direction == "h" else field[r + 1, c]
                fraction = a / (a - b)
                coordinates[key] = (
                    round(xs[c] + (fraction * step if direction == "h" else 0)),
                    round(ys[r] + (fraction * step if direction == "v" else 0)),
                )
            a, b = keys[first], keys[second]
            neighbors[a].append(b)
            neighbors[b].append(a)
    lines = []
    # Walk boundary paths first, then any closed contours.
    starts = [key for key, adjacent in neighbors.items() if len(adjacent) == 1]
    starts.extend(neighbors)
    for start in starts:
        if not neighbors[start]:
            continue
        path, current = [coordinates[start]], start
        while neighbors[current]:
            next_key = neighbors[current].pop()
            neighbors[next_key].remove(current)
            point = coordinates[next_key]
            if point != path[-1]:
                path.append(point)
            current = next_key
        if len(path) > 1:
            lines.append(path)
    return lines


def cross_3d(area, width, density, z):
    """Traverse a bisected triangle lattice through shared edge intersections."""
    x0, y0, x1, y1 = area
    spacing = 1.25 * width / density
    order = max(1, ceil(log2(max(x1 - x0, y1 - y0, spacing) / spacing)))
    if 2 ** (2 * order + 1) > MAX_POINTS:
        raise SliceError("Cross 3D exceeds the path budget. Reduce infill or increase Extrusion Width.")
    side = spacing * 2 ** order
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    low, high = (cx - side / 2, cy - side / 2), (cx + side / 2, cy + side / 2)
    phase = 0.36 * sin(2 * pi * z / (spacing * 4))
    points = []

    def edge_point(a, b):
        a, b = sorted((a, b))
        fraction = 0.5 + phase
        return (round(a[0] + (b[0] - a[0]) * fraction),
                round(a[1] + (b[1] - a[1]) * fraction))

    def visit(a, b, c, enter, leave, depth):
        # Edges are AB, BC, CA, with C at the right angle.
        edges = ((a, b), (b, c), (c, a))
        if not depth:
            for edge in (enter, leave):
                point = edge_point(*edges[edge])
                if not points or points[-1] != point:
                    points.append(point)
            return
        midpoint = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if enter == 2:
            visit(a, c, midpoint, 0, 1, depth - 1)
            visit(c, b, midpoint, 2, 0 if leave == 1 else 1, depth - 1)
        else:
            visit(a, c, midpoint, 2, 1, depth - 1)
            visit(c, b, midpoint, 2, 0, depth - 1)

    visit(low, high, (high[0], low[1]), 2, 0, 2 * order)
    visit(high, low, (low[0], high[1]), 2, 0, 2 * order)
    if points[-1] != points[0]:
        points.append(points[0])
    return [points]


class AdaptiveCubic:
    """Subdivide tilted cubes close to the source surface, up to the base density."""

    def __init__(self, vertices, triangles, width, density):
        self.basis = np.array([(sqrt(2 / 3) * cos(a), sqrt(2 / 3) * sin(a), 1 / sqrt(3))
                               for a in (0, 2 * pi / 3, 4 * pi / 3)])
        self.cell = 3 * width / density * sqrt(2 / 3)
        points = np.asarray(vertices) * SCALE
        rotated = points @ self.basis.T
        self.low = np.floor(rotated.min(axis=0) / self.cell).astype(int)
        span = max(1, ceil(float(np.max(rotated.max(axis=0) / self.cell - self.low))))
        self.span = 2 ** ceil(log2(span))
        self.tree = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
        self.leaves = []

    def build_steps(self):
        pending = [(tuple(self.low), self.span)]
        visited = 0
        while pending:
            low, span = pending.pop()
            center = (np.asarray(low) + span / 2) * self.cell
            _, _, _, distance = self.tree.find_nearest(Vector(center @ self.basis / SCALE))
            near_surface = distance is not None and distance <= sqrt(3) * span * self.cell / (2 * SCALE)
            if span > 1 and (span > 8 or near_surface):
                half = span // 2
                pending.extend(((low[0] + x, low[1] + y, low[2] + z), half)
                               for x in (0, half) for y in (0, half) for z in (0, half))
            else:
                self.leaves.append((low, span))
            visited += 1
            if visited > MAX_POINTS:
                raise SliceError("Adaptive cubic cells exceed the budget. Reduce infill or increase Extrusion Width.")
            if visited % 128 == 0:
                yield 0.35, "Building adaptive cubic cells"
        # Cell ranges let each slice skip cubes that cannot intersect it.
        self.leaves.sort(key=lambda cell: sum(cell[0]))

    def lines(self, z):
        planes = defaultdict(list)
        level = sqrt(3) * z / self.cell
        for low, span in self.leaves:
            if sum(low) > level:
                break
            if sum(low) + 3 * span < level:
                continue
            for axis in range(3):
                a, b = (axis + 1) % 3, (axis + 2) % 3
                for fixed in (low[axis], low[axis] + span):
                    total = level - fixed
                    first = max(low[a], total - low[b] - span)
                    last = min(low[a] + span, total - low[b])
                    if last - first > 1e-8:
                        planes[axis, fixed].append((first, last))
        result = []
        for (axis, fixed), intervals in sorted(planes.items()):
            a, b = (axis + 1) % 3, (axis + 2) % 3
            merged = []
            for start, end in sorted(intervals):
                if merged and start <= merged[-1][1] + 1e-8:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([start, end])
            for start, end in merged:
                line = []
                for value in (start, end):
                    point = np.empty(3)
                    point[axis], point[a], point[b] = fixed, value, level - fixed - value
                    x, y, _ = point @ self.basis * self.cell
                    line.append((round(x), round(y)))
                if line[0] != line[1]:
                    result.append(line)
        return result


def hilbert(area, width):
    x0, y0, x1, y1 = area
    # Overlap the rounded beads at the maze's inside corners.
    spacing = width * 0.70
    side = max(x1 - x0, y1 - y0) + spacing
    order = max(1, ceil(log2(side / spacing)))
    count = 2 ** order
    if count * count > MAX_POINTS:
        raise SliceError("Hilbert surface exceeds the path budget. Increase Extrusion Width.")
    # Extend the square to retain the requested track spacing.
    side = (count - 1) * spacing
    origin_x, origin_y = (x0 + x1 - side) / 2, (y0 + y1 - side) / 2
    points = []
    for distance in range(count * count):
        x = y = 0
        size, remainder = 1, distance
        while size < count:
            rx, ry = (remainder // 2) & 1, (remainder ^ (remainder // 2)) & 1
            if not ry:
                if rx:
                    x, y = size - 1 - x, size - 1 - y
                x, y = y, x
            x, y = x + size * rx, y + size * ry
            remainder //= 4
            size *= 2
        points.append((round(origin_x + x * spacing), round(origin_y + y * spacing)))
    return [points]


def spiral(area, width, *, octagram=False):
    x0, y0, x1, y1 = area
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    radius = hypot(x1 - x0, y1 - y0) / 2 + width
    if octagram:
        # Alternating outer and inner vertices form a simple eight-point star.
        # The pitch accounts for the normal distance between successive turns.
        pitch = width * 0.85 * sqrt(1 + 0.72 ** 2 - 2 * 0.72 * cos(pi / 8)) / (sin(pi / 8) * 0.72)
        turns = ceil(radius / (0.72 * pitch)) + 1
        count = 16 * turns
        if count > MAX_POINTS:
            raise SliceError("Octagram surface exceeds the path budget. Increase Extrusion Width.")
        points = []
        for i in range(count + 1):
            angle = i * pi / 8
            r = pitch * i / 16 * (1 if i % 2 == 0 else 0.72)
            points.append((round(cx + r * cos(angle)), round(cy + r * sin(angle))))
    else:
        # Arc-length-controlled sampling keeps circular tracks visibly rounded.
        angle, limit = 0.0, 2 * pi * radius / width
        points = [(round(cx), round(cy))]
        while angle < limit:
            r = width * angle / (2 * pi)
            angle = min(limit, angle + min(pi / 8, sqrt(0.08 * width / max(width, r))))
            r = width * angle / (2 * pi)
            points.append((round(cx + r * cos(angle)), round(cy + r * sin(angle))))
            if len(points) > MAX_POINTS:
                raise SliceError("Spiral surface exceeds the path budget. Increase Extrusion Width.")
    return [points]
