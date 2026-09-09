"""Screen region definitions for calibrated device profiles."""

from dataclasses import dataclass, asdict


def is_tablet_layout(width: int, height: int, density: int | None = None) -> bool:
    """Match Android's wide-layout boundary without misclassifying dense phones."""
    if density and density > 0:
        smallest_width_dp = min(width, height) * 160 / density
        return smallest_width_dp >= 600
    # Backward-compatible fallback for callers/old profiles without density.
    return width / max(height, 1) >= 0.55


@dataclass
class BBox:
    """Bounding box for a screen region."""
    x: int      # top-left x
    y: int      # top-left y
    w: int      # width
    h: int      # height

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    def as_tuple(self) -> tuple[int, int, int, int]:
        """Return (x1, y1, x2, y2) for PIL cropping."""
        return (self.x, self.y, self.x2, self.y2)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BBox":
        return cls(**d)


@dataclass
class ScreenRegions:
    """All calibrated screen regions and tap targets for a device."""

    # Pokemon detail screen
    name_region: BBox
    cp_region: BBox
    shiny_icon_region: BBox
    shadow_icon_region: BBox
    favorite_star_region: BBox
    lucky_icon_region: BBox
    gender_region: BBox
    weight_label_region: BBox       # "WEIGHT" / "LIGHTEST" / "HEAVIEST"
    height_label_region: BBox       # "HEIGHT" / "SHORTEST" / "TALLEST"
    dynamax_region: BBox            # Area to scan for Dynamax/Gmax purple icon (below type, above stardust)

    # Appraisal flow (multi-step)
    menu_button: tuple[int, int]            # hamburger 3-bar button (bottom right)
    appraise_menu_item: tuple[int, int]     # "Appraise" in the popup menu
    dismiss_professor: tuple[int, int]      # tap to dismiss professor intro dialog

    # Appraisal screen
    atk_bar_region: BBox
    def_bar_region: BBox
    sta_bar_region: BBox
    close_appraisal_target: tuple[int, int]  # legacy name: right-arrow/next target

    # Navigation
    swipe_start: tuple[int, int]
    swipe_end: tuple[int, int]
    swipe_duration_ms: int
    back_button: tuple[int, int]

    # Screen dimensions
    screen_width: int
    screen_height: int

    # Storage navigation targets.  Older profiles omit these, so keep them
    # optional and let GameNavigator fall back to the original Fold6 anchors.
    storage_first_item: tuple[int, int] | None = None
    storage_search_bar: tuple[int, int] | None = None
    storage_search_clear: tuple[int, int] | None = None
    map_pokeball: tuple[int, int] | None = None
    map_pokemon_button: tuple[int, int] | None = None
    # Actual X at the bottom centre of appraisal.  ``close_appraisal_target``
    # is retained for backward compatibility but is the right-arrow/next
    # target, not the close control.
    appraisal_close_x: tuple[int, int] | None = None

    def to_dict(self) -> dict:
        result = {}
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, BBox):
                result[field_name] = value.to_dict()
            else:
                result[field_name] = value
        return result

    @classmethod
    def from_dict(cls, d: dict) -> "ScreenRegions":
        bbox_fields = {
            "name_region", "cp_region", "shiny_icon_region", "shadow_icon_region",
            "favorite_star_region", "lucky_icon_region", "gender_region",
            "weight_label_region", "height_label_region", "dynamax_region",
            "atk_bar_region", "def_bar_region", "sta_bar_region",
        }
        tuple_fields = {
            "menu_button", "appraise_menu_item", "dismiss_professor",
            "close_appraisal_target", "swipe_start", "swipe_end", "back_button",
            "storage_first_item", "storage_search_bar", "storage_search_clear",
            "map_pokeball", "map_pokemon_button", "appraisal_close_x",
        }
        kwargs = {}
        for key, value in d.items():
            if key in bbox_fields:
                kwargs[key] = BBox.from_dict(value)
            elif key in tuple_fields:
                kwargs[key] = tuple(value) if value is not None else None
            else:
                kwargs[key] = value
        return cls(**kwargs)

    @classmethod
    def default_for_resolution(cls, width: int, height: int,
                               density: int | None = None) -> "ScreenRegions":
        """Generate default regions scaled from a Galaxy Z Fold6 cover screen reference.

        Reference device: SM-F956B at 968x2376.
        Coordinates were measured from real screenshots of Pokemon Go.
        """
        w, h = width, height

        # Pokemon Go uses a materially different layout on wide-screen tablets;
        # scaling the narrow Fold6 cover-screen coordinates only by resolution
        # lands on the search field instead of the first Pokemon.  Use anchors
        # measured from a 1440x2304 Samsung tablet and scale that layout as a
        # unit for similarly wide portrait screens.
        if is_tablet_layout(width, height, density):
            sx = w / 1440
            sy = h / 2304
            return cls(
                name_region=BBox(int(390 * sx), int(1120 * sy), int(660 * sx), int(105 * sy)),
                cp_region=BBox(int(430 * sx), int(120 * sy), int(580 * sx), int(130 * sy)),
                shiny_icon_region=BBox(int(250 * sx), int(980 * sy), int(130 * sx), int(130 * sy)),
                shadow_icon_region=BBox(int(70 * sx), int(500 * sy), int(130 * sx), int(130 * sy)),
                favorite_star_region=BBox(int(1240 * sx), int(125 * sy), int(165 * sx), int(165 * sy)),
                lucky_icon_region=BBox(int(65 * sx), int(360 * sy), int(140 * sx), int(140 * sy)),
                gender_region=BBox(int(1130 * sx), int(1260 * sy), int(170 * sx), int(150 * sy)),
                weight_label_region=BBox(int(120 * sx), int(1570 * sy), int(330 * sx), int(100 * sy)),
                height_label_region=BBox(int(990 * sx), int(1570 * sy), int(330 * sx), int(100 * sy)),
                dynamax_region=BBox(int(540 * sx), int(1740 * sy), int(360 * sx), int(190 * sy)),
                menu_button=(int(1270 * sx), int(2100 * sy)),
                appraise_menu_item=(int(980 * sx), int(1650 * sy)),
                dismiss_professor=(int(720 * sx), int(2150 * sy)),
                atk_bar_region=BBox(int(150 * sx), int(1635 * sy), int(455 * sx), int(36 * sy)),
                def_bar_region=BBox(int(150 * sx), int(1758 * sy), int(455 * sx), int(36 * sy)),
                sta_bar_region=BBox(int(150 * sx), int(1882 * sy), int(455 * sx), int(36 * sy)),
                close_appraisal_target=(int(1385 * sx), int(1750 * sy)),
                swipe_start=(int(1180 * sx), int(1120 * sy)),
                swipe_end=(int(260 * sx), int(1120 * sy)),
                swipe_duration_ms=200,
                back_button=(int(120 * sx), int(410 * sy)),
                screen_width=w,
                screen_height=h,
                storage_first_item=(int(240 * sx), int(760 * sy)),
                storage_search_bar=(int(720 * sx), int(405 * sy)),
                storage_search_clear=(int(1340 * sx), int(405 * sy)),
                map_pokeball=(int(720 * sx), int(2100 * sy)),
                map_pokemon_button=(int(320 * sx), int(1775 * sy)),
                appraisal_close_x=(int(720 * sx), int(2191 * sy)),
            )

        # Scale factors relative to 968x2376 (Fold6 cover screen)
        sx = w / 968
        sy = h / 2376

        return cls(
            # Detail screen
            name_region=BBox(
                x=int(220 * sx), y=int(855 * sy),
                w=int(510 * sx), h=int(65 * sy),
            ),
            cp_region=BBox(
                x=int(300 * sx), y=int(100 * sy),
                w=int(380 * sx), h=int(90 * sy),
            ),
            shiny_icon_region=BBox(
                # Shiny sparkle icons appear near the Pokemon name
                x=int(150 * sx), y=int(820 * sy),
                w=int(70 * sx), h=int(70 * sy),
            ),
            shadow_icon_region=BBox(
                # Shadow flame icon near the Pokemon
                x=int(60 * sx), y=int(450 * sy),
                w=int(80 * sx), h=int(80 * sy),
            ),
            favorite_star_region=BBox(
                x=int(855 * sx), y=int(90 * sy),
                w=int(85 * sx), h=int(85 * sy),
            ),
            lucky_icon_region=BBox(
                # Lucky sparkle appears on the background
                x=int(60 * sx), y=int(350 * sy),
                w=int(80 * sx), h=int(80 * sy),
            ),
            gender_region=BBox(
                # Gender symbol (♂/♀) on the white card, right of HP bar
                x=int(780 * sx), y=int(895 * sy),
                w=int(120 * sx), h=int(70 * sy),
            ),
            weight_label_region=BBox(
                # Text below weight value: "WEIGHT" or "LIGHTEST" or "HEAVIEST"
                x=int(30 * sx), y=int(1155 * sy),
                w=int(250 * sx), h=int(50 * sy),
            ),
            height_label_region=BBox(
                # Text below height value: "HEIGHT" or "SHORTEST" or "TALLEST"
                x=int(620 * sx), y=int(1155 * sy),
                w=int(280 * sx), h=int(50 * sy),
            ),
            dynamax_region=BBox(
                # Scan area for Dynamax/Gmax purple icon (between type row and stardust)
                # The icon can move depending on whether Mega is available
                x=int(50 * sx), y=int(1220 * sy),
                w=int(500 * sx), h=int(150 * sy),
            ),

            # Appraisal flow — multi-step
            menu_button=(int(910 * sx), int(2260 * sy)),          # hamburger 3-bar (bottom-right)
            appraise_menu_item=(int(580 * sx), int(1870 * sy)),   # "Appraise" in popup
            dismiss_professor=(int(484 * sx), int(2300 * sy)),    # tap professor text bubble (NOT the X button!)

            # Appraisal screen — IV bars (bar only, no labels)
            # Bars: x=118 to x=446 (328px wide), ~20px tall
            atk_bar_region=BBox(
                x=int(118 * sx), y=int(1827 * sy),
                w=int(328 * sx), h=int(20 * sy),
            ),
            def_bar_region=BBox(
                x=int(118 * sx), y=int(1920 * sy),
                w=int(328 * sx), h=int(20 * sy),
            ),
            sta_bar_region=BBox(
                x=int(118 * sx), y=int(2012 * sy),
                w=int(328 * sx), h=int(20 * sy),
            ),
            close_appraisal_target=(int(920 * sx), int(1750 * sy)),

            # Navigation
            swipe_start=(int(800 * sx), int(h // 2)),
            swipe_end=(int(170 * sx), int(h // 2)),
            # 200 ms animated but bounced back on the Fold6 live gate. A
            # 300 ms gesture advanced and kept the appraisal overlay open.
            swipe_duration_ms=300,
            back_button=(int(50 * sx), int(120 * sy)),

            # Dimensions
            screen_width=w,
            screen_height=h,
            storage_first_item=(int(160 * sx), int(550 * sy)),
            storage_search_bar=(int(484 * sx), int(300 * sy)),
            storage_search_clear=(int(920 * sx), int(300 * sy)),
            map_pokeball=(int(484 * sx), int(2280 * sy)),
            map_pokemon_button=(int(110 * sx), int(1960 * sy)),
            appraisal_close_x=(int(484 * sx), int(2260 * sy)),
        )
