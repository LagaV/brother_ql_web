# Border Area Fixes Summary

## Issues Fixed

### 1. ✅ Enable Flags Not Being Sent (FIXED)
**Problem:** JavaScript was not sending enable flags (`enable_left_area`, `enable_left_bar`, etc.) to the server.

**Fix:** Updated `main.js` line ~118 to include all enable flags in the markdown preview data.

**Result:** Border areas now render correctly!

### 2. ✅ Date Format Issue (FIXED)
**Problem:** Date was showing `06□10□2025` instead of `06.10.2025` due to Unicode encoding issues with `strftime`.

**Fix:** Replaced `strftime('%d.%m.%Y')` with manual string formatting:
```python
date_str = f"{now.day:02d}.{now.month:02d}.{now.year}"
```

**Result:** Dates now display correctly as `06.10.2025`

### 3. ✅ Font Consistency (ALREADY CORRECT)
**Status:** Border areas already use the same font as markdown content via `font_path` parameter.

**How it works:** The `font_path` is passed from the markdown rendering context to `add_border_areas()`, ensuring consistent fonts throughout.

### 4. ✅ Font Size Options for Top/Bottom Text (ADDED)
**Problem:** No way to control font size for top/bottom text areas (only bar text had size controls).

**Fix:** Added new parameters:
- `top_text_size_pt` - Font size for top text area
- `bottom_text_size_pt` - Font size for bottom text area

**Backend changes:**
- Added parameters to `add_border_areas()` function signature
- Updated text rendering to use custom font size if specified, otherwise use default
- Added parameters to routes.py context building
- Added parameters to `add_border_areas()` call in routes.py

**Frontend changes needed:**
- Add UI input fields for `top_text_size_pt` and `bottom_text_size_pt`
- Update JavaScript to send these parameters

## Files Modified

1. **app/markdown_render.py**
   - Fixed date formatting in `process_vars()` function
   - Added `top_text_size_pt` and `bottom_text_size_pt` parameters
   - Updated top/bottom text rendering to use custom font sizes

2. **app/labeldesigner/routes.py**
   - Added `top_text_size_pt` and `bottom_text_size_pt` to context building
   - Added parameters to `add_border_areas()` call

3. **app/labeldesigner/templates/main.js**
   - Added enable flags to markdown preview data (line ~118)

## Still TODO: UI Fields for Font Size

Need to add input fields in the HTML template for:

### Top Area - Text Mode
Add after the "Text" input field:
```html
<div class="form-group col-4">
    <label for="topTextSizePt">Font Size (pt)</label>
    <input id="topTextSizePt" class="form-control form-control-sm" type="number" min="0" max="999" step="0.1" value="0" placeholder="Auto">
</div>
```

### Bottom Area - Text Mode
Add after the "Text" input field:
```html
<div class="form-group col-4">
    <label for="bottomTextSizePt">Font Size (pt)</label>
    <input id="bottomTextSizePt" class="form-control form-control-sm" type="number" min="0" max="999" step="0.1" value="0" placeholder="Auto">
</div>
```

### JavaScript Update
Add to the data object in `main.js` (around line 140):
```javascript
data.top_text_size_pt = $('#enableTopArea').is(':checked') ? ($('#topTextSizePt').val() || 0) : 0;
data.bottom_text_size_pt = $('#enableBottomArea').is(':checked') ? ($('#bottomTextSizePt').val() || 0) : 0;
```

## Testing

1. **Date Format:** Add `{date}` to any border area text - should show `06.10.2025`
2. **Time Format:** Add `{time}` - should show `15:30` (24-hour format)
3. **DateTime Format:** Add `{datetime}` - should show `06.10.2025 15:30`
4. **Font Consistency:** Border text should use the same font as markdown content
5. **Font Size (when UI added):** Setting custom font size should override default

## Current Status

✅ **Working:**
- Border areas render correctly
- Enable flags are sent properly
- Date/time formatting is correct
- Font consistency is maintained
- Backend supports custom font sizes for top/bottom text

⏳ **Pending:**
- UI fields for `top_text_size_pt` and `bottom_text_size_pt` need to be added to HTML
- JavaScript needs to send these new parameters

## How to Complete

1. Find the top area text section in `labeldesigner.html`
2. Add font size input field after the text input
3. Find the bottom area text section
4. Add font size input field after the text input
5. Update JavaScript to send `top_text_size_pt` and `bottom_text_size_pt`
6. Test with different font sizes

## Notes

- Font size of `0` means "use default" (markdown base font size)
- Font sizes are in points (pt), converted to pixels at 300 DPI
- Bar text font sizes already have UI controls and work correctly
- The new text font size controls follow the same pattern as bar text
