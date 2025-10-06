# Border Area Rendering Debug Guide

## Changes Made

I've added comprehensive debug logging to the `add_border_areas()` function in `app/markdown_render.py`. This will help identify why border areas are not rendering.

### Debug Output Added

The function now prints detailed information at each step:

1. **Function Entry**
   - Input image dimensions
   - All area dimensions (mm and px)
   - All enable flags (area, bar, text)

2. **Canvas Creation**
   - Content size
   - Final canvas size
   - Content paste position
   - ImageDraw object creation

3. **Top Area Rendering**
   - Area and bar dimensions
   - Enable flags
   - Bar drawing coordinates
   - Text rendering attempts with error details

4. **Bottom Area Rendering**
   - Similar detailed logging as top area

## How to Test

### Step 1: Run the Application
```bash
cd /Users/ve/OneDrive/GIT/brother_ql_web
python run.py
```

### Step 2: Enable Border Areas in UI
1. Open the label designer
2. Enable a border area (e.g., Top Area)
3. Configure the area:
   - Set area size (e.g., 10mm)
   - Enable either bar or text
   - If bar: set bar size and optionally add text
   - If text: add text content
4. Generate preview

### Step 3: Check Console Output
Look for lines starting with `[BORDER-DEBUG]`:

```
[BORDER-DEBUG] ========== add_border_areas called ==========
[BORDER-DEBUG] Input image size: 640x200
[BORDER-DEBUG] Areas (mm): left=0, right=0, top=10, bottom=0
[BORDER-DEBUG] Enable area flags: left=False, right=False, top=True, bottom=False
[BORDER-DEBUG] Enable bar flags: left=False, right=False, top=True, bottom=False
[BORDER-DEBUG] Enable text flags: left=False, right=False, top=False, bottom=False
[BORDER-DEBUG] Areas (px): left=0, right=0, top=118, bottom=0
[BORDER-DEBUG] Bars (px): left=0, right=0, top=59, bottom=0
[BORDER-DEBUG] Content size: 640x200
[BORDER-DEBUG] Final canvas size: 640x318
[BORDER-DEBUG] Content pasted at (0, 118)
[BORDER-DEBUG] ImageDraw object created: <PIL.ImageDraw.ImageDraw object at 0x...>
[BORDER-DEBUG] TOP AREA: area_px=118, enable_top_area=True, bar_height=59, enable_top_bar=True
[BORDER-DEBUG] TOP AREA: content_x=0, content_width=640, final_height=318
[BORDER-DEBUG] Drawing TOP BAR: x1=0, y1=0, x2=640, y2=59, fill=(0, 0, 0)
[BORDER-DEBUG] TOP BAR drawn successfully
```

## Common Issues to Look For

### Issue 1: Function Not Called
If you don't see `[BORDER-DEBUG] ========== add_border_areas called ==========`:
- The function is not being invoked
- Check that markdown rendering is being used (not plain text/image mode)
- Verify the call in `routes.py` around line 1800

### Issue 2: Enable Flags Are False
If enable flags show as `False` when they should be `True`:
- Check JavaScript is sending correct values
- Check `build_label_context_from_request()` in `routes.py`
- Verify parameter names match between frontend and backend

### Issue 3: Area Dimensions Are Zero
If `Areas (px)` shows zeros:
- Check that area sizes are being set in the UI
- Verify mm-to-px conversion (should be ~118px for 10mm at 300dpi)

### Issue 4: Drawing Commands Execute But Nothing Visible
If you see "drawn successfully" but no visual output:
- Check coordinates are within canvas bounds
- Verify fill color is not white (would be invisible on white background)
- Check if the image is being converted/processed after drawing

### Issue 5: Exception During Drawing
If you see `[BORDER-DEBUG] ERROR drawing...`:
- Read the exception message and traceback
- Common issues:
  - Font file not found
  - Invalid coordinates (negative or out of bounds)
  - Text processing errors

## Next Steps Based on Output

### If function is not called:
Check the call site in `routes.py` in the `create_label_from_context()` function around line 1800-1900.

### If enable flags are wrong:
1. Check browser developer console for JavaScript errors
2. Verify form field names in `labeldesigner.html`
3. Check parameter parsing in `build_label_context_from_request()`

### If drawing succeeds but nothing visible:
1. Save the result image to disk for inspection:
   ```python
   result.save('/tmp/border_debug.png')
   print(f"[BORDER-DEBUG] Saved result to /tmp/border_debug.png")
   ```
2. Check if image is being further processed after `add_border_areas()` returns

### If exceptions occur:
1. Fix the specific error (usually font path or coordinate issues)
2. Re-test

## Expected Successful Output

For a top bar with 10mm area and 5mm bar:
```
[BORDER-DEBUG] ========== add_border_areas called ==========
[BORDER-DEBUG] Input image size: 640x200
[BORDER-DEBUG] Areas (mm): left=0, right=0, top=10, bottom=0
[BORDER-DEBUG] Enable area flags: left=False, right=False, top=True, bottom=False
[BORDER-DEBUG] Enable bar flags: left=False, right=False, top=True, bottom=False
[BORDER-DEBUG] Areas (px): left=0, right=0, top=118, bottom=0
[BORDER-DEBUG] Bars (px): left=0, right=0, top=59, bottom=0
[BORDER-DEBUG] Content size: 640x200
[BORDER-DEBUG] Final canvas size: 640x318
[BORDER-DEBUG] Content pasted at (0, 118)
[BORDER-DEBUG] TOP AREA: area_px=118, enable_top_area=True, bar_height=59, enable_top_bar=True
[BORDER-DEBUG] Drawing TOP BAR: x1=0, y1=0, x2=640, y2=59, fill=(0, 0, 0)
[BORDER-DEBUG] TOP BAR drawn successfully
```

The preview should show:
- Black bar at the top (59px tall)
- White space below bar (59px)
- Content starting at y=118

## Reverting Debug Changes

Once the issue is identified and fixed, you can remove the debug print statements or replace them with proper logging:

```python
import logging
logger = logging.getLogger(__name__)
logger.debug(f"add_border_areas called with top_area={top_area_mm}mm")
```

Then control verbosity via logging configuration instead of removing code.
