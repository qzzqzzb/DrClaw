# PPTXTools - Enhanced PowerPoint shape manipulation utilities

PPTXTools is a Python package that extends python-pptx capabilities with advanced shape manipulation features, including gradient fills, image-filled shapes, and auto-sizing text boxes.

## Features

- Create gradient-filled shapes with both native OOXML and bitmap methods
- Apply gradient backgrounds to slides
- Create solid-colored shapes with precise control over fill and outline
- Fill shapes with images (stretch or tile modes)
- Create text boxes with auto-sizing text and borders

## Installation

```bash
pip install pptx-tools
```

## Quick Start

```python
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches
from pptx_tools import add_gradient_shape, add_gradient_background

# Create a presentation
prs = Presentation()
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout

# Add a gradient background
add_gradient_background(
    slide,
    color_start="#FFD54F",
    color_end="#9E9E9E",
    angle_deg=90
)

# Add a gradient-filled rounded rectangle
shape = add_gradient_shape(
    slide,
    shape_type=MSO_SHAPE.ROUNDED_RECTANGLE,
    left=Inches(1),
    top=Inches(1),
    width=Inches(4),
    height=Inches(2),
    color_start="#2196F3",
    color_end="#F44336",
    angle_deg=45
)

# Save the presentation
prs.save("gradient_example.pptx")
```

## Documentation

### add_gradient_shape
Add a shape with gradient fill to a PowerPoint slide.
```python
shape = add_gradient_shape(
    slide,
    shape_type=MSO_SHAPE.RECTANGLE,
    left=Inches(1),
    top=Inches(1),
    width=Inches(4),
    height=Inches(2),
    color_start="#FFD54F",
    color_end="#9E9E9E",
    angle_deg=0,
    method="ooxml"  # or "picture"
)
```

### add_gradient_background
Apply a gradient background to an entire slide.
```python
add_gradient_background(
    slide,
    color_start="#FFD54F",
    color_end="#9E9E9E",
    angle_deg=90,
    method="ooxml"
)
```

For more examples and detailed API documentation, visit our [GitHub repository](https://github.com/yourusername/pptx-tools).

## Requirements

- Python 3.7+
- python-pptx>=0.6.21
- Pillow>=9.0.0
- lxml>=4.9.0

## License

This project is licensed under the MIT License - see the LICENSE file for details.