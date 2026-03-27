"""Screen region definitions for calibrated device profiles."""

from dataclasses import dataclass, asdict


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
    close_appraisal_target: tuple[int, int]  # tap to close appraisal (> arrow)

    # Navigation
    swipe_start: tuple[int, int]
    swipe_end: tuple[int, int]
    swipe_duration_ms: int
    back_button: tuple[int, int]

    # Screen dimensions
    screen_width: int
    screen_height: int

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
        }
        kwargs = {}
        for key, value in d.items():
            if key in bbox_fields:
                kwargs[key] = BBox.from_dict(value)
            elif key in tuple_fields:
                kwargs[key] = tuple(value)
            else:
                kwargs[key] = value
        return cls(**kwargs)

    @classmethod
    def default_for_resolution(cls, width: int, height: int) -> "ScreenRegions":
        """Generate default regions scaled from a Galaxy Z Fold6 cover screen reference.

        Reference device: SM-F956B at 968x2376.
        Coordinates were measured from real screenshots of Pokemon Go.
        """
        w, h = width, height
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
            close_appraisal_target=(int(920 * sx), int(1750 * sy)),  # > arrow right side

            # Navigation
            swipe_start=(int(800 * sx), int(h // 2)),
            swipe_end=(int(170 * sx), int(h // 2)),
            swipe_duration_ms=200,
            back_button=(int(50 * sx), int(120 * sy)),

            # Dimensions
            screen_width=w,
            screen_height=h,
        )
