"""Make reader sprite templates for the Jinto monsters from the pictures in a copy of batomon.com.

    python build_jinto_sprites.py path/to/batomon-showdown

The site's 48x48 sprites are drawn 4x in the game. Check done 2026-11-15 on the 95 saved Pantra team-slot
sprites: a site sprite scaled 4x and pasted at (-12, -52) onto a white 160x104 patch reproduces the saved
template pixel for pixel (difference 0.0) for most monsters (the rest bob a few pixels), and for shop-card
cut-outs a 4x sprite seen through the card window starting 42 px down (the outline's bounding box) matched
159 of 203 saved cards. So templates can be made without seeing the monster in a live run.
Writes templates/sprites/<Name>.png, <Name>@shiny.png and templates/card_sprites/<Name>.png (never over
an existing file). Monsters of data/jinto_monsters.csv and data/foreign_monsters.csv only.
"""
import csv
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
OFFSET = (-12, -52)
CARD_TOP = 42


def big(sp: Image.Image) -> Image.Image:
    return sp.convert("RGBA").resize((192, 192), Image.NEAREST)


def slot_patch(sp: Image.Image) -> Image.Image:
    layer = Image.new("RGBA", (160, 104), (0, 0, 0, 0))
    layer.paste(big(sp), OFFSET)
    canvas = Image.new("RGBA", (160, 104), (255, 255, 255, 255))
    canvas.alpha_composite(layer)
    return canvas.convert("RGB")


def card_cutout(sp: Image.Image):
    win = np.asarray(big(sp))[CARD_TOP:CARD_TOP + 104]
    ys, xs = np.nonzero(win[..., 3] > 127)
    if len(xs) == 0:
        return None
    return Image.fromarray(win[ys.min():ys.max() + 1, xs.min():xs.max() + 1])


def main(site: str) -> None:
    site = Path(site)
    names = set()
    for f in ("jinto_monsters.csv", "foreign_monsters.csv"):
        with open(ROOT / "data" / f, newline="", encoding="utf-8") as fh:
            names |= {r["name"] for r in csv.DictReader(fh)}
    slug = {m["name"]: m["slug"] for m in json.load(open(site / "monsters.json", encoding="utf-8"))}
    made = 0
    for n in sorted(names):
        s = slug.get(n)
        if not s:
            continue
        for suffix, img in (("", s), ("@shiny", s + "_shiny")):
            src = site / "images" / "monsters" / f"{img}.png"
            if not src.exists():
                continue
            sp = Image.open(src)
            for folder, make in (("sprites", slot_patch), ("card_sprites", card_cutout)):
                if folder == "card_sprites" and suffix:
                    continue                       # the shop shows normal-palette cards; Shiny cards are learned live
                out = ROOT / "templates" / folder / f"{n}{suffix}.png"
                if out.exists() or any((ROOT / "templates" / folder).glob(f"{n}{suffix}__*.png")):
                    continue
                im = make(sp)
                if im is None:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                im.save(out)
                made += 1
    print(made, "templates written")


if __name__ == "__main__":
    main(sys.argv[1])
