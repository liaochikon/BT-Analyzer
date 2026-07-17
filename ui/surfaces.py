"""Small composable layout surfaces for DearPyGui content."""

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto

import dearpygui.dearpygui as dpg


class CenterAxes(Enum):
    HORIZONTAL = auto()
    VERTICAL = auto()
    BOTH = auto()


@dataclass(frozen=True, slots=True)
class _CenteredSurface:
    outer_tag: int | str
    content_tag: int | str
    horizontal_spacer_tag: int | str
    vertical_spacer_tag: int | str
    axes: CenterAxes
    padding: tuple[int, int]


_centered_surfaces: dict[int | str, _CenteredSurface] = {}


def _center_offset(available: float, content: float, padding: int) -> int:
    return max(int(padding), int((available - content) / 2))


@contextmanager
def canvas_surface(*, tag=0, border=False):
    """Yield a child container that fills its parent's remaining content area."""
    surface_tag = tag or dpg.generate_uuid()
    with dpg.child_window(
        tag=surface_tag,
        width=-1,
        height=-1,
        border=border,
        no_scrollbar=True,
        no_scroll_with_mouse=True,
    ):
        yield surface_tag


@contextmanager
def centered_surface(
    *, tag=0, axes=CenterAxes.BOTH, padding=(16, 16), show=True, parent=0):
    """Yield content centered on selected axes within a fill child container."""
    if not isinstance(axes, CenterAxes):
        raise TypeError("axes must be a CenterAxes value")
    outer_tag = tag or dpg.generate_uuid()
    vertical_spacer_tag = dpg.generate_uuid()
    horizontal_spacer_tag = dpg.generate_uuid()
    content_tag = dpg.generate_uuid()
    pad_x = max(0, int(padding[0]))
    pad_y = max(0, int(padding[1]))

    with dpg.child_window(
        tag=outer_tag,
        parent=parent,
        width=-1,
        height=-1,
        border=False,
        show=show,
    ):
        dpg.add_spacer(tag=vertical_spacer_tag, height=pad_y)
        with dpg.group(horizontal=True):
            dpg.add_spacer(tag=horizontal_spacer_tag, width=pad_x)
            with dpg.group(tag=content_tag):
                yield content_tag

    _centered_surfaces[outer_tag] = _CenteredSurface(
        outer_tag=outer_tag,
        content_tag=content_tag,
        horizontal_spacer_tag=horizontal_spacer_tag,
        vertical_spacer_tag=vertical_spacer_tag,
        axes=axes,
        padding=(pad_x, pad_y),
    )


def update_surfaces():
    """Re-center live surfaces after DearPyGui has laid out the current frame."""
    for outer_tag, surface in list(_centered_surfaces.items()):
        if not dpg.does_item_exist(outer_tag):
            _centered_surfaces.pop(outer_tag, None)
            continue
        if not dpg.is_item_shown(outer_tag):
            continue
        outer_width, outer_height = dpg.get_item_rect_size(outer_tag)
        content_width, content_height = dpg.get_item_rect_size(surface.content_tag)
        pad_x, pad_y = surface.padding
        horizontal = (pad_x if surface.axes is CenterAxes.VERTICAL else
                      _center_offset(outer_width, content_width, pad_x))
        vertical = (pad_y if surface.axes is CenterAxes.HORIZONTAL else
                    _center_offset(outer_height, content_height, pad_y))
        dpg.configure_item(surface.horizontal_spacer_tag, width=horizontal)
        dpg.configure_item(surface.vertical_spacer_tag, height=vertical)