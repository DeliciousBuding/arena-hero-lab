"""视野与视线计算。

- 视野：曼哈顿半径 + supercover 整数线；线恰好穿过两格角落时两格都计入，
  任一侧障碍都阻挡（对应官方 map-and-vision 规则）。
- Ranger 射击：只检查直线（水平/垂直/45° 对角）上的中间格，角落旁格不阻挡
  （对应官方 combat 规则）。
"""


def supercover_line(x0, y0, x1, y1):
    """返回从 (x0,y0) 到 (x1,y1) 的整数 supercover 线格列表（含两端点）。

    45° 线穿过角落时同时输出两个相邻格。
    """
    dx = x1 - x0
    dy = y1 - y0
    nx = abs(dx)
    ny = abs(dy)
    sign_x = 1 if dx > 0 else -1 if dx < 0 else 0
    sign_y = 1 if dy > 0 else -1 if dy < 0 else 0
    pts = [(x0, y0)]
    ix = iy = 0
    while ix < nx or iy < ny:
        decision = (1 + 2 * ix) * ny - (1 + 2 * iy) * nx
        if decision == 0:
            previous_x, previous_y = x0, y0
            x0 += sign_x
            ix += 1
            pts.append((x0, previous_y))
            pts.append((previous_x, previous_y + sign_y))
            y0 += sign_y
            iy += 1
            pts.append((x0, y0))
        elif decision < 0:
            x0 += sign_x
            ix += 1
            pts.append((x0, y0))
        else:
            y0 += sign_y
            iy += 1
            pts.append((x0, y0))
    return pts


def shot_intermediate_cells(x0, y0, x1, y1):
    """返回射击路径上的中间格（不含两端）。仅当水平/垂直/45° 对齐时有效。"""
    dx = x1 - x0
    dy = y1 - y0
    nx = abs(dx)
    ny = abs(dy)
    sign_x = 1 if dx > 0 else -1 if dx < 0 else 0
    sign_y = 1 if dy > 0 else -1 if dy < 0 else 0
    cells = []
    for t in range(1, max(nx, ny)):
        cells.append((x0 + sign_x * t, y0 + sign_y * t))
    return cells


def is_shot_line(x0, y0, x1, y1, max_range):
    """(x0,y0)->(x1,y1) 是否为合法射击线（八方向、距离 1..max_range）。"""
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    if dx == 0 and dy == 0:
        return False
    if dx != 0 and dy != 0 and dx != dy:
        return False
    r = max(dx, dy)
    return 1 <= r <= max_range


_VIS_CACHE_MAX = 50000  # 单个地形对象的缓存上限（正常一局约 1.3k 条）


def visible_cells(px, py, radius, is_obstacle):
    """返回从 (px,py) 可见的格（曼哈顿半径内、无遮挡）。

    is_obstacle(x, y) -> bool。可见性边界：能看到障碍格本身，但看不到其背后。

    结果按 (px, py, radius) 缓存在地形对象（is_obstacle 的宿主）上：地形永久不变，
    所以同一世界内该函数是纯函数。返回**只读元组**，调用方不得修改。
    """
    cache = None
    owner = getattr(is_obstacle, "__self__", None)
    if owner is not None:
        cache = getattr(owner, "_vis_cache", None)
        if cache is None:
            cache = {}
            try:
                owner._vis_cache = cache
            except AttributeError:  # __slots__ 宿主：退化为不缓存
                cache = None
    if cache is not None:
        hit = cache.get((px, py, radius))
        if hit is not None:
            return hit
    out = _trace_visible(px, py, radius, is_obstacle)
    if cache is not None:
        if len(cache) >= _VIS_CACHE_MAX:
            cache.clear()
        cache[(px, py, radius)] = out
    return out


def _trace_visible(px, py, radius, is_obstacle):
    out = []
    for dy in range(-radius, radius + 1):
        rem = radius - abs(dy)
        for dx in range(-rem, rem + 1):
            tx = px + dx
            ty = py + dy
            blocked = False
            # The obstacle target cell itself is visible; only intermediate
            # cells block it.  This also keeps the endpoint out of the LOS
            # test now that supercover_line correctly includes both endpoints.
            for cx, cy in supercover_line(px, py, tx, ty)[1:-1]:
                if is_obstacle(cx, cy):
                    blocked = True
                    break
            if not blocked:
                out.append((tx, ty))
    return tuple(out)
