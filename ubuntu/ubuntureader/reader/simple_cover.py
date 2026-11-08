"""Fixed-size fallback cover; metadata never contributes to widget measurement."""
import gi
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo


def simple_cover(title, authors, width, height):
    cover = Gtk.DrawingArea(content_width=width, content_height=height,
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                            hexpand=False, vexpand=False)
    cover.update_property([Gtk.AccessibleProperty.LABEL], [title+' — '+authors])

    def draw(widget, context, allocated_width, allocated_height):
        # DrawingArea's intrinsic size is independent of either text layout.
        context.save()
        context.rectangle(0, 0, width, height)
        context.clip()
        context.set_source_rgb(.914, .878, .800)
        context.paint()
        context.set_source_rgba(.208, .235, .282, .18)
        context.rectangle(0, 0, 4, height)
        context.fill()
        small = width < 100
        padding = 7 if small else 14
        author_height = 24 if small else 36
        title_height = height-author_height-2*padding
        for text, size, weight, top, area_height in [
                (title, 10 if small else 13, Pango.Weight.BOLD, padding, title_height),
                (authors, 8 if small else 10, Pango.Weight.NORMAL, height-padding-author_height, author_height)]:
            layout = PangoCairo.create_layout(context)
            font = Pango.FontDescription('IPAexGothic')
            font.set_absolute_size(size*Pango.SCALE)
            font.set_weight(weight)
            layout.set_font_description(font)
            layout.set_text(text, -1)
            layout.set_width((width-2*padding)*Pango.SCALE)
            layout.set_height(area_height*Pango.SCALE)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
            layout.set_alignment(Pango.Alignment.CENTER)
            _, text_height = layout.get_pixel_size()
            context.set_source_rgb(.208, .235, .282)
            context.move_to(padding, top+max(0,(area_height-text_height)/2))
            PangoCairo.show_layout(context, layout)
        context.restore()

    cover.set_draw_func(draw)
    return cover
