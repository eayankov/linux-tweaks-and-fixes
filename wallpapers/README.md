# Plasma light/dark wallpapers

Each package contains one light and one dark wallpaper. Image symlinks point
back to `~/Изображения/Wallpapers/Light` and `~/Изображения/Wallpapers/Dark`;
keep those original files in place. Images are named using their actual dimensions.
The relative links assume this repository is at `~/repos/linux-tweaks-and-fixes`.

The 17 packages use all 17 light and 13 dark images, pairing them in filename
order and repeating the first four dark images to cover the remaining light images.

## Install

The packages are linked individually into `~/.local/share/wallpapers/`.
To install again from this directory, run:

```bash
mkdir -p "$HOME/.local/share/wallpapers"
for package in "$PWD"/egor-wallpaper-pair-*; do
    ln -s "$package" "$HOME/.local/share/wallpapers/"
done
```

## Configure Plasma

1. Right-click the desktop and open **Configure Desktop and Wallpaper**.
2. Select **Slideshow**.
3. Add this repository's `wallpapers` directory as the only slideshow folder.
4. Set **Switch dynamic wallpapers** to **Based on whether the Plasma style is light or dark**.
5. Choose the interval and ordering, then apply. Repeat for other desktops/screens if needed.

Alternatively, select one package with the **Image** wallpaper type.
The automatic variant setting follows Plasma's light/dark style; changing only
an application color scheme while using a fixed Plasma style may not change it.

## Pairs

| Package | Light | Dark |
| --- | --- | --- |
| egor-wallpaper-pair-01 | 1-christina-gottardi-unsplash.jpg | 0-ship-at-sea.jpg |
| egor-wallpaper-pair-02 | 1-kanagawa.jpg | 0-swirl-buck.jpg |
| egor-wallpaper-pair-03 | Billy Hauling 1887.jpg | 1-dark-waters.jpg |
| egor-wallpaper-pair-04 | View of Venice #1 1500.jpg | 1-sunset-lake.png |
| egor-wallpaper-pair-05 | View of Venice #2 1500.jpg | 2-pawel-czerwinski.jpg |
| egor-wallpaper-pair-06 | View of Venice #3 1500.jpg | eclipse1.jpg |
| egor-wallpaper-pair-07 | View of Venice #4 1500.jpg | eclipse2.jpg |
| egor-wallpaper-pair-08 | View of Venice #5 1500.jpg | eclipse3.jpg |
| egor-wallpaper-pair-09 | View of Venice #6 1500.jpg | eclipse4.jpg |
| egor-wallpaper-pair-10 | c4-spring-sakura-sky.jpg | signal-enthusiast.jpg |
| egor-wallpaper-pair-11 | cats-anime.jpg | sun1.jpg |
| egor-wallpaper-pair-12 | derwent-water-with-skiddaw-in-the-distance-between-1795-1796.jpg | sun2.jpg |
| egor-wallpaper-pair-13 | japanese-castle-pixel-digital-art.jpg | sun3.jpg |
| egor-wallpaper-pair-14 | matlock-dale-between-1780-1785.jpg | 0-ship-at-sea.jpg |
| egor-wallpaper-pair-15 | moscow.jpg | 0-swirl-buck.jpg |
| egor-wallpaper-pair-16 | road.jpg | 1-dark-waters.jpg |
| egor-wallpaper-pair-17 | salty-suburban.jpg | 1-sunset-lake.png |
