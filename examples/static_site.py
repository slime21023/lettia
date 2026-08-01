import os
import tempfile

from lettia import App, Context, Response
from lettia.ext import StaticFiles

# Create a temporary web root directory for demonstration
tmp_dir = tempfile.TemporaryDirectory()
web_root = tmp_dir.name

# Write sample files
with open(os.path.join(web_root, "index.html"), "w", encoding="utf-8") as f:
    f.write("<!DOCTYPE html><html><body><h1>Welcome to Lettia!</h1></body></html>")

with open(os.path.join(web_root, "style.css"), "w", encoding="utf-8") as f:
    f.write("body { font-family: sans-serif; background: #111; color: #fff; }")

app = App()
static_handler = StaticFiles(directory=web_root, html=True)


@app.get("/*filepath")
async def serve_static(ctx: Context) -> Response:
    return await static_handler.handle(ctx)


if __name__ == "__main__":
    import uvicorn

    print(f"Serving static site from {web_root} on http://127.0.0.1:8000 ...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
