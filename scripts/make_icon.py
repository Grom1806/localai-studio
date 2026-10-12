"""Draw the LocalAI Studio app icon (violet→blue rounded square with a white 4-point sparkle)."""
import math

from PIL import Image, ImageDraw, ImageFilter

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# gradient background
grad = Image.new("RGBA", (S, S))
px = grad.load()
c1, c2 = (139, 108, 255), (91, 140, 255)
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        px[x, y] = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)) + (255,)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=230, fill=255)
img.paste(grad, (0, 0), mask)
# soft inner glow
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(glow).ellipse([260, 220, 780, 740], fill=(255, 255, 255, 70))
img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(90)))


def sparkle(cx, cy, r, w):
    pts = []
    for k in range(8):
        ang = math.pi / 4 * k - math.pi / 2
        rad = r if k % 2 == 0 else w
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


d = ImageDraw.Draw(img)
d.polygon(sparkle(S / 2, S / 2 + 10, 330, 78), fill=(255, 255, 255, 255))
d.polygon(sparkle(S * 0.74, S * 0.28, 120, 30), fill=(255, 255, 255, 235))
d.polygon(sparkle(S * 0.27, S * 0.74, 80, 20), fill=(255, 255, 255, 210))
img.save(r"C:\LocalAI\ui\public\icon-512.png") if False else None
img.resize((512, 512), Image.LANCZOS).save(r"C:\LocalAI\config\localai.png")
img.save(r"C:\LocalAI\config\localai.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon written")
