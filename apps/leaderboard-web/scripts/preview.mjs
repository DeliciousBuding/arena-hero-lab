/**
 * 本地静态预览：pnpm preview（等效 npx serve out，但自动剥离 GitHub Pages 的
 * basePath 前缀，并用目录索引解析无扩展名路径）。
 *
 * 用法：pnpm build && pnpm preview   →   http://localhost:4173/
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("../out/", import.meta.url));
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "/arena-hero-lab";
const PORT = Number(process.env.PORT ?? 4173);

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};

const server = createServer(async (req, res) => {
  try {
    // 剥离 basePath 前缀（与 GitHub Pages 子路径部署一致）
    let path = decodeURIComponent(new URL(req.url ?? "/", "http://localhost").pathname);
    if (path.startsWith(BASE_PATH)) path = path.slice(BASE_PATH.length);
    if (path === "/") path = "/index.html";

    let filePath = join(ROOT, normalize(path));
    // 无扩展名：优先目录索引（/leaderboard → /leaderboard/index.html），
    // 否则回退扁平 html（Next 16 导出形态 /leaderboard → /leaderboard.html）。
    // 尾斜杠与无尾斜杠等价：/platform/ 与 /platform 都解析到 platform.html，
    // 否则 `${filePath}.html` 会拼出 `platform\.html`（目录内隐藏文件）而 404。
    if (!extname(filePath)) {
      const dirIndex = join(filePath, "index.html");
      const flat = `${filePath.replace(/[\\/]+$/, "")}.html`;
      filePath = (await readFile(dirIndex).catch(() => null))
        ? dirIndex
        : flat;
    }

    const body = await readFile(filePath);
    res.writeHead(200, { "Content-Type": MIME[extname(filePath)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("404 Not Found");
  }
});

server.listen(PORT, () => {
  console.log(`[preview] serving ./out at http://localhost:${PORT} (basePath ${BASE_PATH} 已剥离)`);
});
