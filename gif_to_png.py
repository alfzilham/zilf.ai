from PIL import Image
import math
import os
import sys

gif_path = r"d:\2026\Workspace\Website\.hams.ai\agent\static\image\orb-reference.gif"
if not os.path.exists(gif_path):
    print("Cannot find GIF!")
    sys.exit(1)

gif = Image.open(gif_path)
frames = []
try:
    while True:
        frames.append(gif.copy())
        gif.seek(len(frames))
except EOFError:
    pass

if len(frames) == 0:
    print("No frames found!")
    sys.exit(1)

num_frames = 12
step = max(1, len(frames) // num_frames)
sampled_frames = frames[::step][:num_frames]

width, height = sampled_frames[0].size
grid_cols = 4
grid_rows = math.ceil(len(sampled_frames) / grid_cols)
grid_width = width * grid_cols
grid_height = height * grid_rows

combined = Image.new("RGB", (grid_width, grid_height), (0, 0, 0))

for i, frame in enumerate(sampled_frames):
    x = (i % grid_cols) * width
    y = (i // grid_cols) * height
    frame = frame.convert("RGB")
    combined.paste(frame, (x, y))

out_path = r"d:\2026\Workspace\Website\.hams.ai\agent\static\image\orb-reference-grid.png"
combined.save(out_path)
print(f"Saved {len(sampled_frames)} frames as grid to {out_path}")
