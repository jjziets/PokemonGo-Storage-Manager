"""Qt table model for the Pokemon collection with sprite images."""

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QSize
from PySide6.QtGui import QColor, QPixmap, QIcon

from ...data.models import Pokemon
from ...sprites import get_sprite_path


COLUMNS = [
    ("", "sprite"),              # sprite image
    ("Species", "species"),
    ("CP", "cp"),
    ("ATK", "atk"),
    ("DEF", "def_"),
    ("STA", "sta"),
    ("IV%", "iv_pct"),
    ("Gender", "gender"),
    ("Shiny", "shiny"),
    ("Shadow", "shadow"),
    ("Lucky", "lucky"),
    ("Fav", "favorited"),
    ("Dmax", "is_dynamax"),
    ("W.Tag", "weight_tag"),
    ("H.Tag", "height_tag"),
    ("Decision", "decision"),
    ("Reason", "decision_reason"),
]

# Sprite cache
_sprite_cache: dict[str, QPixmap | None] = {}


def _get_sprite_pixmap(species: str) -> QPixmap | None:
    if species not in _sprite_cache:
        path = get_sprite_path(species)
        if path and path.exists():
            pix = QPixmap(str(path))
            _sprite_cache[species] = pix.scaled(40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            _sprite_cache[species] = None
    return _sprite_cache[species]


def _iv_color(pct: float) -> QColor:
    if pct >= 0.96:
        return QColor(80, 200, 80)
    elif pct >= 0.82:
        return QColor(120, 180, 80)
    elif pct >= 0.67:
        return QColor(200, 180, 60)
    elif pct >= 0.51:
        return QColor(200, 140, 60)
    return QColor(180, 80, 80)


def _decision_color(decision: str | None) -> QColor | None:
    if decision == "KEEP":
        return QColor(60, 150, 60)
    elif decision == "TRANSFER":
        return QColor(150, 60, 60)
    return None


class PokemonTableModel(QAbstractTableModel):
    """Table model wrapping a list of Pokemon objects with sprite images."""

    def __init__(self, pokemon: list[Pokemon] | None = None, parent=None):
        super().__init__(parent)
        self._data: list[Pokemon] = pokemon or []

    def set_data(self, pokemon: list[Pokemon]):
        self.beginResetModel()
        self._data = list(pokemon)
        self.endResetModel()

    def get_pokemon(self, row: int) -> Pokemon | None:
        if 0 <= row < len(self._data):
            return self._data[row]
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._data):
            return None

        p = self._data[index.row()]
        col_name = COLUMNS[index.column()][1]

        # Sprite column
        if col_name == "sprite":
            if role == Qt.DecorationRole:
                pix = _get_sprite_pixmap(p.species)
                if pix:
                    return pix
            return None

        if role == Qt.DisplayRole:
            val = getattr(p, col_name, "")
            if col_name == "iv_pct":
                return f"{val:.0%}"
            if col_name in ("shiny", "shadow", "lucky", "favorited", "is_dynamax"):
                return "Yes" if val else ""
            if col_name in ("pvp_rank_gl", "pvp_rank_ul"):
                return str(val) if val else ""
            if val is None:
                return ""
            return str(val)

        elif role == Qt.BackgroundRole:
            if col_name == "iv_pct":
                return _iv_color(p.iv_pct)
            if col_name == "decision":
                c = _decision_color(p.decision)
                if c:
                    return c

        elif role == Qt.ForegroundRole:
            if col_name == "shiny" and p.shiny:
                return QColor(255, 215, 0)
            if col_name == "shadow" and p.shadow:
                return QColor(180, 100, 220)
            if col_name == "is_dynamax" and p.is_dynamax:
                return QColor(255, 100, 100)

        elif role == Qt.TextAlignmentRole:
            if col_name in ("cp", "atk", "def_", "sta", "iv_pct"):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        elif role == Qt.UserRole:
            return getattr(p, col_name, "")

        return None

    def sort(self, column, order=Qt.AscendingOrder):
        self.beginResetModel()
        col_name = COLUMNS[column][1]
        if col_name == "sprite":
            col_name = "species"
        reverse = (order == Qt.DescendingOrder)

        def sort_key(p):
            val = getattr(p, col_name, "")
            if val is None:
                return (1, "")
            if isinstance(val, bool):
                return (0, int(val))
            if isinstance(val, (int, float)):
                return (0, val)
            return (0, str(val).lower())

        self._data.sort(key=sort_key, reverse=reverse)
        self.endResetModel()
