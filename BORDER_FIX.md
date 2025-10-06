# Border Area Rendering Fix

## Problem
Border areas are generated (canvas is adjusted) but boxes and text are not rendered in preview.

## Root Cause
The `add_border_areas()` function in `markdown_render.py` has the correct structure, but there are several issues:

### Issue 1: Enable Flag Logic
The function checks enable flags correctly, but the rendering might fail silently due to exceptions being caught with bare `except:` clauses.

### Issue 2: Debug Output
The print statement on line ~650 shows TOP AREA is being processed, but we need to verify the actual drawing happens.

### Issue 3: Coordinate Calculations
The bars and text positioning might be calculated incorrectly, placing them outside the visible canvas.

## Solution

### Step 1: Add Debug Logging
Replace print statements with proper logging to see what's happening:

```python
# In add_border_areas(), replace:
print(f"TOP AREA: area_px={top_area_px}, enable_top_area={enable_top_area}, bar_height={bar_height}, enable_top_bar={enable_top_bar}")

# With:
import logging
logger = logging.getLogger(__name__)
logger.info(f"[border-debug] TOP AREA: area_px={top_area_px}, enable={enable_top_area}, bar_h={bar_height}, enable_bar={enable_top_bar}, enable_text={enable_top_text}")
```

### Step 2: Fix Exception Handling
Replace bare `except:` with specific exception handling and logging:

```python
# Instead of:
except:
    pass

# Use:
except Exception as e:
    logger.error(f"[border-debug] Failed to render top bar text: {e}", exc_info=True)
```

### Step 3: Verify Drawing Coordinates
Add logging before each draw operation:

```python
# Before drawing rectangles:
logger.info(f"[border-debug] Drawing top bar: x1={bar_x1}, y1=0, x2={bar_x2}, y2={bar_height}, fill={top_bar_fill}")
draw.rectangle([(bar_x1, 0), (bar_x2, bar_height)], fill=top_bar_fill)
```

### Step 4: Check Enable Flag Propagation
Verify that enable flags are being passed from the UI through to the function:

1. Check JavaScript sends correct values
2. Check `build_label_context_from_request()` reads them correctly
3. Check they're passed to `add_border_areas()`

## Testing Steps

1. Enable top area with bar
2. Check logs for:
   - "TOP AREA: area_px=X" message
   - "Drawing top bar" message
   - Any exception messages
3. If coordinates are logged but nothing renders, check:
   - Are coordinates within canvas bounds?
   - Is the fill color correct (not white on white)?
   - Is the draw object valid?

## Quick Fix to Try First

Add this at the start of `add_border_areas()` to verify it's being called:

```python
logger = logging.getLogger(__name__)
logger.info(f"[border-debug] add_border_areas called: left_area={left_area_px}, right_area={right_area_px}, top_area={top_area_px}, bottom_area={bottom_area_px}")
logger.info(f"[border-debug] Enable flags: left={enable_left_area}, right={enable_right_area}, top={enable_top_area}, bottom={enable_bottom_area}")
logger.info(f"[border-debug] Bar enables: left_bar={enable_left_bar}, right_bar={enable_right_bar}, top_bar={enable_top_bar}, bottom_bar={enable_bottom_bar}")
logger.info(f"[border-debug] Text enables: left_text={enable_left_text}, right_text={enable_right_text}, top_text={enable_top_text}, bottom_text={enable_bottom_text}")
```

This will show if the function is being called with correct parameters.
