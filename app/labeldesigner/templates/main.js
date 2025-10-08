var dropZoneMode = 'preview';
var imageDropZone;
var markdownRenderInFlight = false;
var pendingFontStyle = null;

// PDF page navigation state
var pdfCurrentPage = 1;
var pdfTotalPages = 1;
var isPdfLoaded = false;
var uploadedImageFile = null;  // Store reference to uploaded file for page navigation

function updateHeadWidth() {
    var option = $("#labelSize option:selected");
    var width = option.data("head-width");
    if (width) {
        $("#headWidth").text(width);
    } else {
        $("#headWidth").text("?");
    }
}

var initializing = true;
var storageAvailable = false;
var storageReady = false;
var persistTimer = null;
var markdownButtonDefaultHtml = null;
var markdownButtonResetTimer = null;
var markdownPreviewPages = [];
var markdownCurrentPage = 0;
const STORAGE_VERSION = 1;
const STORAGE_KEY = 'brotherQlLabelDesignerSettings_v' + STORAGE_VERSION;
const RED_SUPPORT = {{ 'true' if red_support else 'false' }};

function formData(cut_once) {
    var text = $('#labelText').val();
    if (text === '') {
        text = ' ';
    }
    var data = {
        text:        text,
        font_family: $('#fontFamily option:selected').text(),
        font_style:  $('#fontStyle').val(),
        font_size:   $('#fontSize').val(),
        label_size:  $('#labelSize').val(),
        align:       getCheckedValue('fontAlign'),
        orientation: getCheckedValue('orientation'),
        margin_top:    $('#marginTop').val(),
        margin_bottom: $('#marginBottom').val(),
        margin_left:   $('#marginLeft').val(),
        margin_right:  $('#marginRight').val(),
        print_type:    getCheckedValue('printType'),
        qrcode_size:   $('#qrCodeSize').val(),
        qrcode_correction: $('#qrCodeCorrection').val(),
        image_bw_threshold: $('#imageBwThreshold').val(),
        image_mode:         getCheckedValue('imageMode'),
        image_rotate_90:    $('#imageRotate90').is(':checked') ? 1 : 0,
        image_stretch_length: $('#imageStretchLength').is(':checked') ? 1 : 0,
        no_crop:            $('#imageNoCrop').is(':checked') ? 1 : 0,
        image_crop_left:    $('#imageCropLeft').val() || 0,
        image_crop_right:   $('#imageCropRight').val() || 0,
        image_crop_top:     $('#imageCropTop').val() || 0,
        image_crop_bottom:  $('#imageCropBottom').val() || 0,
        print_count:       $('#printCount').val(),
        line_spacing:      getCheckedValue('lineSpacing'),
        cut_once:          cut_once ? 1 : 0,
        printer_id:        $('#printerSelect').val()
    };
    if (RED_SUPPORT) {
        data.print_color = getCheckedValue('printColor');
    }
    data.markdown_paged = markdownPagedEnabled() ? 1 : 0;
    data.markdown_slice_mm = $('#markdownSliceMm').val();
    data.markdown_page_numbers = markdownPageNumbersEnabled() ? 1 : 0;
    data.markdown_page_circle = $('#markdownPageCircle').is(':checked') ? 1 : 0;
    data.markdown_page_number_mm = $('#markdownPageNumberMm').val();
    data.markdown_page_count = $('#markdownPageCount').is(':checked') ? 1 : 0;

    // Unified page range handling for both markdown and PDF
    var pageFrom = $('#pageFrom').val();
    var pageTo = $('#pageTo').val();

    if (isPdfLoaded) {
        // PDF mode: use page range for printing, pdf_page for preview
        if (typeof dropZoneMode !== 'undefined' && dropZoneMode === 'print') {
            if (pageFrom && pageTo) {
                data.page_from = pageFrom;
                data.page_to = pageTo;
            } else {
                data.pdf_page = pdfCurrentPage;
            }
        } else {
            // Preview mode - use current page
            data.pdf_page = pdfCurrentPage;
        }
    } else {
        // Markdown mode: use page range if specified
        if (pageFrom) {
            data.page_from = pageFrom;
        }
        if (pageTo) {
            data.page_to = pageTo;
        }
    }

    // Track current markdown preview page so empty range prints the visible page
    if (getCheckedValue('printType') === 'markdown') {
        if (markdownPreviewPages.length > 0) {
            data.markdown_page = markdownCurrentPage + 1;
        } else {
            data.markdown_page = 1;
        }
    }

    // Border areas for markdown - with enable flags
    data.enable_left_area = $('#enableLeftArea').is(':checked') ? 1 : 0;
    data.enable_right_area = $('#enableRightArea').is(':checked') ? 1 : 0;
    data.enable_top_area = $('#enableTopArea').is(':checked') ? 1 : 0;
    data.enable_bottom_area = $('#enableBottomArea').is(':checked') ? 1 : 0;
    data.enable_left_bar = (getCheckedValue('leftContentType') === 'bar') ? 1 : 0;
    data.enable_left_text = (getCheckedValue('leftContentType') === 'text') ? 1 : 0;
    data.enable_right_bar = (getCheckedValue('rightContentType') === 'bar') ? 1 : 0;
    data.enable_right_text = (getCheckedValue('rightContentType') === 'text') ? 1 : 0;
    data.enable_top_bar = (getCheckedValue('topContentType') === 'bar') ? 1 : 0;
    data.enable_top_text = (getCheckedValue('topContentType') === 'text') ? 1 : 0;
    data.enable_bottom_bar = (getCheckedValue('bottomContentType') === 'bar') ? 1 : 0;
    data.enable_bottom_text = (getCheckedValue('bottomContentType') === 'text') ? 1 : 0;
    
    data.left_area_mm = $('#enableLeftArea').is(':checked') ? ($('#leftAreaMm').val() || 0) : 0;
    data.right_area_mm = $('#enableRightArea').is(':checked') ? ($('#rightAreaMm').val() || 0) : 0;
    data.top_area_mm = $('#enableTopArea').is(':checked') ? ($('#topAreaMm').val() || 0) : 0;
    data.bottom_area_mm = $('#enableBottomArea').is(':checked') ? ($('#bottomAreaMm').val() || 0) : 0;
    data.left_bar_mm = $('#enableLeftArea').is(':checked') ? ($('#leftBarMm').val() || 0) : 0;
    data.right_bar_mm = $('#enableRightArea').is(':checked') ? ($('#rightBarMm').val() || 0) : 0;
    data.top_bar_mm = $('#enableTopArea').is(':checked') ? ($('#topBarMm').val() || 0) : 0;
    data.bottom_bar_mm = $('#enableBottomArea').is(':checked') ? ($('#bottomBarMm').val() || 0) : 0;
    data.left_bar_text_size_pt = $('#enableLeftArea').is(':checked') ? ($('#leftBarTextSizePt').val() || 0) : 0;
    data.right_bar_text_size_pt = $('#enableRightArea').is(':checked') ? ($('#rightBarTextSizePt').val() || 0) : 0;
    data.top_bar_text_size_pt = $('#enableTopArea').is(':checked') ? ($('#topBarTextSizePt').val() || 0) : 0;
    data.bottom_bar_text_size_pt = $('#enableBottomArea').is(':checked') ? ($('#bottomBarTextSizePt').val() || 0) : 0;
    data.top_text_size_pt = $('#enableTopArea').is(':checked') ? ($('#topTextSizePt').val() || 0) : 0;
    data.bottom_text_size_pt = $('#enableBottomArea').is(':checked') ? ($('#bottomTextSizePt').val() || 0) : 0;
    data.left_bar_color = $('#enableLeftArea').is(':checked') ? (getCheckedValue('leftBarColor') || 'black') : 'black';
    data.right_bar_color = $('#enableRightArea').is(':checked') ? (getCheckedValue('rightBarColor') || 'black') : 'black';
    data.top_bar_color = $('#enableTopArea').is(':checked') ? (getCheckedValue('topBarColor') || 'black') : 'black';
    data.bottom_bar_color = $('#enableBottomArea').is(':checked') ? (getCheckedValue('bottomBarColor') || 'black') : 'black';
    data.left_bar_text = $('#enableLeftArea').is(':checked') ? ($('#leftBarText').val() || '') : '';
    data.right_bar_text = $('#enableRightArea').is(':checked') ? ($('#rightBarText').val() || '') : '';
    data.left_text = $('#enableLeftArea').is(':checked') ? ($('#leftText').val() || '') : '';
    data.right_text = $('#enableRightArea').is(':checked') ? ($('#rightText').val() || '') : '';
    data.top_bar_text = $('#enableTopArea').is(':checked') ? ($('#topBarText').val() || '') : '';
    data.bottom_bar_text = $('#enableBottomArea').is(':checked') ? ($('#bottomBarText').val() || '') : '';
    data.top_text = $('#enableTopArea').is(':checked') ? ($('#topText').val() || '') : '';
    data.bottom_text = $('#enableBottomArea').is(':checked') && !$('#bottomShowPageNumbers').is(':checked') ? ($('#bottomText').val() || '') : '';
    data.top_divider = $('#enableTopArea').is(':checked') && $('#topDivider').is(':checked') ? 1 : 0;
    data.bottom_divider = $('#enableBottomArea').is(':checked') && $('#bottomDivider').is(':checked') ? 1 : 0;
    data.bottom_show_page_numbers = $('#enableBottomArea').is(':checked') && $('#bottomShowPageNumbers').is(':checked') ? 1 : 0;
    data.bottom_page_number_mm = $('#bottomPageNumberMm').val() || 4;

    return data;
}

function updatePreview(data) {
    $('#previewImg').attr('src', 'data:image/png;base64,' + data);
    var img = $('#previewImg')[0];
    img.onload = function() {
        updateHeadWidth();
        $('#labelWidth').html((img.naturalWidth / {{default_dpi}} * 2.54).toFixed(1));
        $('#labelHeight').html((img.naturalHeight / {{default_dpi}} * 2.54).toFixed(1));
    };
}

function updateStyles() {
    var font_family = $('#fontFamily option:selected').text();

    // When 'Noto' is selected, specifically request 'Noto Sans' from the backend
    // This ensures we get the general text font, not symbol fonts.
    var requested_font_family = font_family;
    if (font_family === 'Noto') {
        requested_font_family = 'Noto Sans';
    }

    $.ajax({
        type:        'POST',
        url:         '{{url_for('.get_font_styles')}}',
        contentType: 'application\/x-www-form-urlencoded; charset=UTF-8',
        data:        {font: requested_font_family},
        success: function(data) { // 'data' is now an array of style names (JSON array)
            var styleSelect = $('#fontStyle');
            styleSelect.empty();

            var firstStyle = null;
            $.each(data, function(index, styleName) { // Iterate over array
                styleSelect.append($("<option></option>").attr("value", styleName).text(styleName));
                if (firstStyle === null) {
                    firstStyle = styleName;
                }
            });

            var desired = null;
            if (pendingFontStyle && data.includes(pendingFontStyle)) {
                desired = pendingFontStyle;
            // Prioritize 'Regular' or 'Book' if available and no pending style
            } else if (data.includes('Regular')) {
                desired = 'Regular';
            } else if (data.includes('Book')) {
                desired = 'Book';
            } else {
                desired = firstStyle;
            }

            pendingFontStyle = null;
            if (desired) {
                styleSelect.val(desired);
            }
            styleSelect.trigger('change');
        }
    });
}

function toggleMarkdownButton(show) {
    var button = $('#renderMarkdownButton');
    if (!button.length) {
        return;
    }
    // Show/hide button based on mode and auto-render state
    if (show) {
        if (markdownAutoRenderEnabled()) {
            button.hide(); // Hide when auto-render is enabled
        } else {
            button.show();
            button.removeClass('btn-outline-secondary').addClass('btn-primary');
            button.prop('disabled', false);
            button.removeAttr('title');
        }
        if (markdownRenderInFlight && markdownAutoRenderEnabled()) {
            finishMarkdownRenderFeedback();
        }
    } else {
        if (markdownRenderInFlight) {
            finishMarkdownRenderFeedback();
        }
        button.hide();
    }
}

function toggleHeaderArea(area) {
    var checkbox = $('#enable' + area.charAt(0).toUpperCase() + area.slice(1) + 'Area');
    var options = $('#' + area + 'AreaOptions');
    if (checkbox.is(':checked')) {
        options.show();
    } else {
        options.hide();
    }
}

function toggleBorderContent(area, type) {
    // area: 'left', 'right', 'top', 'bottom'
    // type: 'bar', 'text'
    var checkbox = $('#enable' + area.charAt(0).toUpperCase() + area.slice(1) + type.charAt(0).toUpperCase() + type.slice(1));
    var content = $('#' + area + type.charAt(0).toUpperCase() + type.slice(1) + 'Content');
    var otherType = (type === 'bar') ? 'text' : 'bar';
    var otherCheckbox = $('#enable' + area.charAt(0).toUpperCase() + area.slice(1) + otherType.charAt(0).toUpperCase() + otherType.slice(1));
    var otherContent = $('#' + area + otherType.charAt(0).toUpperCase() + otherType.slice(1) + 'Content');

    if (checkbox.is(':checked')) {
        // Show this content
        content.show();
        // Uncheck and hide the other type (mutual exclusivity)
        otherCheckbox.prop('checked', false);
        otherContent.hide();
    } else {
        // Hide this content
        content.hide();
    }
}

function toggleBorderContentType(area, type) {
    // area: 'left', 'right', 'top', 'bottom'
    // type: 'none', 'bar', 'text'
    var barContent = $('#' + area + 'BarContent');
    var textContent = $('#' + area + 'TextContent');

    // If selecting bar or text, automatically enable the area if not already enabled
    if (type !== 'none') {
        var enableCheckbox = $('#enable' + area.charAt(0).toUpperCase() + area.slice(1) + 'Area');
        if (!enableCheckbox.is(':checked')) {
            enableCheckbox.prop('checked', true);
            toggleHeaderArea(area);  // Show the options panel
        }
    }

    // Remove active class from all buttons
    $('#' + area + 'ContentNone').removeClass('active');
    $('#' + area + 'ContentBar').removeClass('active');
    $('#' + area + 'ContentText').removeClass('active');

    // Add active class to selected button
    if (type === 'none') {
        $('#' + area + 'ContentNone').addClass('active');
    } else if (type === 'bar') {
        $('#' + area + 'ContentBar').addClass('active');
    } else if (type === 'text') {
        $('#' + area + 'ContentText').addClass('active');
    }

    // Hide all content sections
    barContent.hide();
    textContent.hide();

    // Show selected content section and set default values if needed
    if (type === 'bar') {
        barContent.show();
        // Set default bar width if currently 0
        var barWidthField = $('#' + area + 'BarMm');
        if (parseFloat(barWidthField.val()) === 0) {
            var isVertical = (area === 'left' || area === 'right');
            barWidthField.val(isVertical ? '5' : '3');
        }
        // Set default area width if currently 0
        var areaWidthField = $('#' + area + 'AreaMm');
        if (parseFloat(areaWidthField.val()) === 0) {
            var isVertical = (area === 'left' || area === 'right');
            areaWidthField.val(isVertical ? '10' : '8');
        }
    } else if (type === 'text') {
        textContent.show();
        // Set default area width if currently 0
        var areaWidthField = $('#' + area + 'AreaMm');
        if (parseFloat(areaWidthField.val()) === 0) {
            var isVertical = (area === 'left' || area === 'right');
            areaWidthField.val(isVertical ? '10' : '8');
        }
    }
    // If 'none', both remain hidden
}

function onBottomPageNumbersToggle() {
    if ($('#bottomShowPageNumbers').is(':checked')) {
        $('#bottomPageNumberOptions').show();
        $('#bottomTextOptions').hide();
    } else {
        $('#bottomPageNumberOptions').hide();
        $('#bottomTextOptions').show();
    }
}

function toggleMarkdownOptions(show) {
    var container = $('#markdownOptions');
    if (!container.length) {
        return;
    }
    if (show) {
        container.show();
        $('#pageRange').show();
    } else {
        container.hide();
        $('#markdownPagedInputs').hide();
        $('#markdownPageNumberInputs').hide();
        if (!(getCheckedValue('printType') === 'image' && isPdfLoaded)) {
            $('#pageRange').hide();
        }
    }
    enforceMarkdownPagingRules();
    updateMarkdownPager();
}

function updateOrientationRestrictions() {
    var printType = getCheckedValue('printType');
    var isImage = (printType === 'image');

    // Disable rotated mode for images (use rotate 90° instead)
    if (isImage) {
        var rotatedBtn = $('#orientation_rotated');
        rotatedBtn.addClass('disabled').prop('disabled', true);
        rotatedBtn.find('input').prop('disabled', true);

        // If rotated was selected, switch to standard
        if (getCheckedValue('orientation') === 'rotated') {
            $('#orientation_standard').addClass('active').find('input').prop('checked', true);
            $('#orientation_rotated').removeClass('active');
            preview();
        }
    } else {
        // Enable rotated mode for other types
        var rotatedBtn = $('#orientation_rotated');
        rotatedBtn.removeClass('disabled').prop('disabled', false);
        rotatedBtn.find('input').prop('disabled', false);
    }
}

function markdownAutoRenderEnabled() {
    var checkbox = $('#markdownAutoRender');
    return checkbox.length ? checkbox.is(':checked') : false;
}

function normalizeFooterInput() {
    var input = $('#markdownFooterMm');
    if (!input.length) {
        return 0;
    }
    var value = parseFloat(input.val());
    if (isNaN(value)) {
        value = 0;
    }
    value = Math.max(0, value);
    input.val(value);
    return value;
}

function enforceMarkdownPagingRules() {
    var checkbox = $('#markdownPaged');
    if (!checkbox.length) {
        return;
    }

    var isMarkdown = (getCheckedValue('printType') === 'markdown');
    var isRotated = (getCheckedValue('orientation') === 'rotated');
    var mustPaginate = isMarkdown && isRotated;
    var stateChanged = false;

    if (mustPaginate) {
        if (!checkbox.prop('checked')) {
            checkbox.prop('checked', true);
            stateChanged = true;
        }
        if (!checkbox.prop('disabled')) {
            checkbox.prop('disabled', true);
            stateChanged = true;
        }
        checkbox.attr('title', 'Paged mode is required when using rotated markdown.');
    } else {
        if (checkbox.prop('disabled')) {
            checkbox.prop('disabled', false);
            stateChanged = true;
        }
        checkbox.removeAttr('title');
    }

    var showPagedInputs = checkbox.is(':checked') && isMarkdown;
    $('#markdownPagedInputs').toggle(showPagedInputs);

    var allowPageNumbers = isMarkdown && showPagedInputs;
    var pageNumberCheckbox = $('#markdownPageNumbers');
    if (pageNumberCheckbox.length) {
        if (!allowPageNumbers) {
            if (pageNumberCheckbox.prop('checked')) {
                pageNumberCheckbox.prop('checked', false);
                stateChanged = true;
            }
            pageNumberCheckbox.prop('disabled', true).attr('title', 'Page numbers require paged markdown.');
        } else {
            if (pageNumberCheckbox.prop('disabled')) {
                pageNumberCheckbox.prop('disabled', false).removeAttr('title');
                stateChanged = true;
            }
        }
    }

    $('#markdownPageNumberInputs').toggle(allowPageNumbers && markdownPageNumbersEnabled());

    if (stateChanged) {
        schedulePersist();
    }
}

function markdownPagedEnabled() {
    var checkbox = $('#markdownPaged');
    return checkbox.length ? checkbox.is(':checked') : false;
}

function onMarkdownPagedToggle() {
    var btn = $('#markdownPagedBtn');
    var checkbox = $('#markdownPaged');

    if (checkbox.is(':checked')) {
        btn.addClass('active');
        $('#markdownPagedInputs').show();
    } else {
        btn.removeClass('active');
        $('#markdownPagedInputs').hide();
    }

    enforceMarkdownPagingRules();
    preview();
}

function onMarkdownAutoRenderToggle() {
    var btn = $('#markdownAutoRenderBtn');
    var checkbox = $('#markdownAutoRender');

    if (checkbox.is(':checked')) {
        btn.addClass('active');
    } else {
        btn.removeClass('active');
    }

    schedulePersist();
    toggleMarkdownButton(getCheckedValue('printType') === 'markdown');
    if (markdownAutoRenderEnabled()) {
        preview(true);
    }
}

function markdownPageNumbersEnabled() {
    var checkbox = $('#markdownPageNumbers');
    return checkbox.length ? checkbox.is(':checked') : false;
}

function onMarkdownPageNumbersToggle() {
    $('#markdownPageNumberInputs').toggle(markdownPagedEnabled() && markdownPageNumbersEnabled());
    schedulePersist();
    preview();
}

function markdownPageCountEnabled() {
    var checkbox = $('#markdownPageCount');
    return checkbox.length ? checkbox.is(':checked') : false;
}

function startMarkdownRenderFeedback() {
    var button = $('#renderMarkdownButton');
    if (!button.length) {
        return;
    }
    if (!markdownButtonDefaultHtml) {
        markdownButtonDefaultHtml = button.html();
    }
    if (markdownButtonResetTimer) {
        clearTimeout(markdownButtonResetTimer);
        markdownButtonResetTimer = null;
    }
    button.prop('disabled', true);
    button.html('<span class="fas fa-sync fa-spin" aria-hidden="true"></span> Rendering...');
    markdownRenderInFlight = true;
}

function finishMarkdownRenderFeedback(messageHtml) {
    var button = $('#renderMarkdownButton');
    if (!button.length) {
        return;
    }
    var html = messageHtml || markdownButtonDefaultHtml;
    if (html) {
        button.html(html);
    }
    // Re-apply disabled state if auto-render is enabled
    if (markdownAutoRenderEnabled()) {
        button.prop('disabled', true);
    } else {
        button.prop('disabled', false);
    }
    markdownRenderInFlight = false;
}

function clearMarkdownPreviewState(options) {
    var keepPageRange = options && options.keepPageRange;
    markdownPreviewPages = [];
    markdownCurrentPage = 0;
    updateMarkdownPager();
    if (!keepPageRange) {
        $('#pageRange').hide();
    }
}

function normalizePageRange(totalPages) {
    var fromInput = $('#pageFrom');
    var toInput = $('#pageTo');
    if (!fromInput.length || !toInput.length) {
        return;
    }

    var total = parseInt(totalPages, 10);
    if (isNaN(total) || total < 1) {
        return;
    }

    var fromRaw = fromInput.val();
    var toRaw = toInput.val();
    var hasFrom = fromRaw !== '' && fromRaw !== null;
    var hasTo = toRaw !== '' && toRaw !== null;

    if (!hasFrom && !hasTo) {
        return;
    }

    var fromVal = parseInt(fromRaw, 10);
    var toVal = parseInt(toRaw, 10);
    var fromInRange = hasFrom && !isNaN(fromVal) && fromVal >= 1 && fromVal <= total;
    var toInRange = hasTo && !isNaN(toVal) && toVal >= 1 && toVal <= total;

    if (hasFrom && hasTo && !fromInRange && !toInRange) {
        fromInput.val('');
        toInput.val('');
        return;
    }

    if (hasFrom) {
        if (isNaN(fromVal)) {
            fromVal = 1;
        }
        fromVal = Math.min(Math.max(fromVal, 1), total);
        fromInput.val(fromVal);
    }

    if (hasTo) {
        if (isNaN(toVal)) {
            toVal = total;
        }
        toVal = Math.min(Math.max(toVal, 1), total);
        toInput.val(toVal);
    }

    var adjustedFrom = parseInt(fromInput.val(), 10);
    var adjustedTo = parseInt(toInput.val(), 10);

    if (!isNaN(adjustedFrom) && !isNaN(adjustedTo) && adjustedFrom > adjustedTo) {
        if (hasFrom && hasTo) {
            fromInput.val(Math.min(adjustedFrom, adjustedTo));
            toInput.val(Math.max(adjustedFrom, adjustedTo));
        } else if (hasFrom) {
            toInput.val(adjustedFrom);
        } else if (hasTo) {
            fromInput.val(adjustedTo);
        }
    }
}

function clearPageRangeInputs() {
    $('#pageFrom').val('');
    $('#pageTo').val('');
    schedulePersist();
}

function normalizePreviewPages(raw) {
    if (raw == null) {
        return [];
    }

    if (typeof raw === 'string') {
        var trimmed = raw.trim();
        if (trimmed.startsWith('{')) {
            try {
                var parsed = JSON.parse(trimmed);
                if (parsed && Array.isArray(parsed.pages)) {
                    return parsed.pages;
                }
            } catch (e) {
                // fall through to treat as single base64 string
            }
        }
        return trimmed ? [trimmed] : [];
    }

    if (Array.isArray(raw)) {
        return raw;
    }

    if (typeof raw === 'object' && raw !== null) {
        if (Array.isArray(raw.pages)) {
            return raw.pages;
        }
        if (raw.image) {
            return [raw.image];
        }
    }

    return [];
}

function updateMarkdownPager() {
    var pager = $('#markdownPager');
    if (!pager.length) {
        return;
    }
    if (markdownPreviewPages.length <= 1) {
        pager.hide();
        $('#markdownPageIndicator').text('Page 1 / 1');
        return;
    }

    pager.show();
    var indicator = $('#markdownPageIndicator');
    indicator.text('Page ' + (markdownCurrentPage + 1) + ' / ' + markdownPreviewPages.length);
    $('#markdownPrevPage').prop('disabled', markdownCurrentPage <= 0);
    $('#markdownNextPage').prop('disabled', markdownCurrentPage >= markdownPreviewPages.length - 1);
}

function showMarkdownPage(index) {
    if (!markdownPreviewPages.length) {
        clearMarkdownPreviewState();
        return;
    }
    var clamped = Math.max(0, Math.min(index, markdownPreviewPages.length - 1));
    markdownCurrentPage = clamped;
    updatePreview(markdownPreviewPages[clamped]);
    updateMarkdownPager();
}

function markdownPrevPage() {
    if (markdownPreviewPages.length) {
        showMarkdownPage(markdownCurrentPage - 1);
    }
}

function markdownNextPage() {
    if (markdownPreviewPages.length) {
        showMarkdownPage(markdownCurrentPage + 1);
    }
}

function handlePreviewResponse(raw, isMarkdown) {
    // Handle error responses
    if (raw && raw.error) {
        console.log('Preview error:', raw.error);
        $('#sourceDimensions').hide();
        clearMarkdownPreviewState();
        return;
    }

    var pages = normalizePreviewPages(raw);
    if (!pages.length) {
        if (isMarkdown || pages.length > 1) {
            clearMarkdownPreviewState();
        }
        $('#sourceDimensions').hide();
        return;
    }

    // Display source dimensions if available
    if (raw && raw.source_width_mm && raw.source_height_mm) {
        $('#sourceWidth').text(raw.source_width_mm);
        $('#sourceHeight').text(raw.source_height_mm);
        $('#sourceDimensions').show();
    } else {
        $('#sourceDimensions').hide();
    }

    // Handle PDF page info
    if (raw && raw.pdf_page_count && raw.pdf_page_count > 1) {
        var currentPage = raw.pdf_current_page || 1;
        setPdfPageInfo(currentPage, raw.pdf_page_count);
    } else if (isPdfLoaded && (!raw || !raw.pdf_page_count)) {
        // PDF was removed, hide navigation
        isPdfLoaded = false;
        $('#pdfPageNavigation').hide();
    }

    // Use pager for markdown or multipage images (e.g., multipage PDFs)
    if (isMarkdown || pages.length > 1) {
        var previousPage = markdownCurrentPage;
        markdownPreviewPages = pages;
        showMarkdownPage(Math.min(previousPage, pages.length - 1));
        // Show page range selector for multipage content
        if (pages.length > 1) {
            $('#pageRange').show();
        }
        normalizePageRange(pages.length);
    } else {
        clearMarkdownPreviewState({ keepPageRange: isPdfLoaded });
        updatePreview(pages[0]);
    }
}

function preview(forceRender) {
    updateHeadWidth();
    var printType = getCheckedValue('printType');
    var isMarkdown = (printType === 'markdown');
    var force = forceRender === true;

    toggleMarkdownButton(isMarkdown);
    toggleMarkdownOptions(isMarkdown);

    if (!isMarkdown) {
        var keepPageRange = (printType === 'image' && isPdfLoaded);
        clearMarkdownPreviewState({ keepPageRange: keepPageRange });
    }

    if ($('#labelSize option:selected').data('round') === 'True') {
        $('img#previewImg').addClass('roundPreviewImage');
    } else {
        $('img#previewImg').removeClass('roundPreviewImage');
    }

    if (getCheckedValue('orientation') === 'standard') {
        $('.marginsTopBottom').prop('disabled', false).removeAttr('title');
        $('.marginsLeftRight').prop('disabled', true).prop('title', 'Only relevant if rotated orientation is selected.');
    } else {
        $('.marginsTopBottom').prop('disabled', true).prop('title', 'Only relevant if standard orientation is selected.');
        $('.marginsLeftRight').prop('disabled', false).removeAttr('title');
    }

    enforceMarkdownPagingRules();

    if (RED_SUPPORT) {
        var labelSizeVal = $('#labelSize').val() || '';
        if (labelSizeVal.includes('red')) {
            $('#print_color_black').removeClass('disabled');
            $('#print_color_red').removeClass('disabled');
            $('#image_mode_red_and_black').removeClass('disabled');
            $('#image_mode_colored').removeClass('disabled');
        } else {
            $('#print_color_black').addClass('disabled').prop('active', true);
            $('#print_color_red').addClass('disabled');
            $('#image_mode_red_and_black').addClass('disabled');
            $('#image_mode_colored').addClass('disabled');
        }
    }

    if (printType === 'image') {
        $('#groupLabelText').hide();
        $('#groupLabelImage').show();
        // Clear any markdown preview content when switching to image mode
        clearMarkdownPreviewState({ keepPageRange: isPdfLoaded });
        $('#previewImg').attr('src', '');
    } else {
        $('#groupLabelText').show();
        $('#groupLabelImage').hide();
        // Hide page range when switching away from image/markdown mode and no PDF/multipage content
        if (!isPdfLoaded) {
            $('#pageRange').hide();
        }
    }

    if (printType === 'image') {
        dropZoneMode = 'preview';
        if (imageDropZone) {
            // If we have a stored file (for PDF page navigation), send it directly
            if (uploadedImageFile && isPdfLoaded) {
                var formDataObj = new FormData();
                formDataObj.append('image', uploadedImageFile);

                // Add all other form parameters
                var fd = formData(false);
                $.each(fd, function(key, value) { formDataObj.append(key, value); });

                // Send via AJAX (simple example - server expects form-encoded or file)
                $.ajax({
                    url: '{{url_for('.get_preview_from_image')}}?return_format=base64',
                    type: 'POST',
                    data: formDataObj,
                    contentType: false,
                    processData: false,
                    success: function(data) { handlePreviewResponse(data, false); }
                });
            } else {
                // No stored file to send; preview will proceed via standard flow
            }
        }
        return;
    }

    if (isMarkdown && !force && !markdownAutoRenderEnabled()) {
        return;
    }

    $.ajax({
        type:        'POST',
        url:         '{{url_for('.get_preview_from_image')}}?return_format=base64',
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        data:        formData(),
        success: function(data) {
            handlePreviewResponse(data, isMarkdown);
        },
        error: function(xhr) {
            if (isMarkdown) {
                var msg = xhr && xhr.responseText ? $('<div>').text(xhr.responseText).html() : 'Unknown error';
                finishMarkdownRenderFeedback('<span class="fas fa-exclamation-triangle" aria-hidden="true"></span> Render Failed');
                // Reset the render button after a short period
                if (markdownButtonResetTimer) {
                    clearTimeout(markdownButtonResetTimer);
                }
                markdownButtonResetTimer = setTimeout(function() {
                    finishMarkdownRenderFeedback();
                }, 4000);
                console && console.warn && console.warn('Markdown preview failed:', msg);
                clearMarkdownPreviewState();
            }
        }
    }).always(function() {
        if (isMarkdown && force && markdownRenderInFlight) {
            finishMarkdownRenderFeedback();
        }
    });
}

function renderMarkdown() {
    if (getCheckedValue('printType') !== 'markdown') {
        return;
    }
    startMarkdownRenderFeedback();
    preview(true);
}

function setStatus(data) {
    if (data['success']) {
        $('#statusPanel').html('<div id="statusBox" class="alert alert-success" role="alert"><i class="fas fa-check"></i><span>Printing was successful.</span></div>');
    } else {
        $('#statusPanel').html('<div id="statusBox" class="alert alert-warning" role="alert"><i class="fas fa-exclamation-triangle"></i><span>Printing was unsuccessful:<br />' + data['message'] + '</span></div>');
    }
    $('#printButton').prop('disabled', false);
    $('#dropdownPrintButton').prop('disabled', false);
}

function print(cut_once) {
    if (typeof cut_once === 'undefined') {
        cut_once = false;
    }
    $('#printButton').prop('disabled', true);
    $('#dropdownPrintButton').prop('disabled', true);
    $('#statusPanel').html('<div id="statusBox" class="alert alert-info" role="alert"><i class="fas fa-hourglass-half"></i><span>Processing print request...</span></div>');

    if (getCheckedValue('printType') === 'image') {
        dropZoneMode = 'print';
        console.log('[print] Set dropZoneMode to print, isPdfLoaded:', isPdfLoaded);
        console.log('[print] PDF page range:', $('#pdfPrintFrom').val(), '-', $('#pdfPrintTo').val());
        if (imageDropZone) {
            imageDropZone.processQueue();
        }
        return;
    }

    $.ajax({
        type:     'POST',
        dataType: 'json',
        data:     formData(cut_once),
        url:      '{{url_for('.print_text')}}',
        success:  setStatus,
        error:    setStatus
    });
}

function getCheckedValue(name) {
    var el = $('input[name=' + name + ']:checked');
    return el.length ? el.val() : null;
}

function setRadioGroup(name, value) {
    if (value === null || typeof value === 'undefined') {
        return;
    }
    var inputs = $('input[name=' + name + ']');
    if (!inputs.length) {
        return;
    }
    inputs.each(function() {
        var match = $(this).val() === value;
        $(this).prop('checked', match);
        var label = $(this).closest('label');
        if (label.length) {
            label.toggleClass('active', match);
        }
    });
}

function collectSettings() {
    var textarea = $('#labelText')[0];
    var caret = 0;
    if (textarea && typeof textarea.selectionStart === 'number') {
        caret = textarea.selectionStart;
    }
    return {
        text: $('#labelText').val(),
        font_family: $('#fontFamily').val(),
        font_style: $('#fontStyle').val(),
        font_size: $('#fontSize').val(),
        label_size: $('#labelSize').val(),
        align: getCheckedValue('fontAlign'),
        orientation: getCheckedValue('orientation'),
        margin_top: $('#marginTop').val(),
        margin_bottom: $('#marginBottom').val(),
        margin_left: $('#marginLeft').val(),
        margin_right: $('#marginRight').val(),
        print_type: getCheckedValue('printType'),
        qrcode_size: $('#qrCodeSize').val(),
        qrcode_correction: $('#qrCodeCorrection').val(),
        image_mode: getCheckedValue('imageMode'),
        image_bw_threshold: $('#imageBwThreshold').val(),
        image_rotate_90: $('#imageRotate90').is(':checked') ? 1 : 0,
        image_stretch_length: $('#imageStretchLength').is(':checked') ? 1 : 0,
        no_crop: $('#imageNoCrop').is(':checked') ? 1 : 0,
        image_crop_left: $('#imageCropLeft').val(),
        image_crop_right: $('#imageCropRight').val(),
        image_crop_top: $('#imageCropTop').val(),
        image_crop_bottom: $('#imageCropBottom').val(),
        print_count: $('#printCount').val(),
        line_spacing: getCheckedValue('lineSpacing'),
        {% if red_support %}
        print_color: getCheckedValue('printColor'),
        {% endif %}
        label_text_scroll: caret,
        markdown_auto_render: markdownAutoRenderEnabled() ? 1 : 0,
        markdown_paged: markdownPagedEnabled() ? 1 : 0,
        markdown_slice_mm: $('#markdownSliceMm').val(),
        markdown_page_numbers: markdownPageNumbersEnabled() ? 1 : 0,
        markdown_page_circle: $('#markdownPageCircle').is(':checked') ? 1 : 0,
        markdown_page_number_mm: $('#markdownPageNumberMm').val(),
        markdown_page_count: $('#markdownPageCount').is(':checked') ? 1 : 0,
        // Border areas with radio-based content type selection
        enable_left_area: $('#enableLeftArea').is(':checked') ? 1 : 0,
        enable_left_bar: (getCheckedValue('leftContentType') === 'bar') ? 1 : 0,
        enable_left_text: (getCheckedValue('leftContentType') === 'text') ? 1 : 0,
        left_area_mm: $('#leftAreaMm').val(),
        left_bar_mm: $('#leftBarMm').val(),
        left_bar_text_size_pt: $('#leftBarTextSizePt').val(),
        left_bar_text: $('#leftBarText').val(),
        left_text: $('#leftText').val(),
        left_bar_color: getCheckedValue('leftBarColor'),

        enable_right_area: $('#enableRightArea').is(':checked') ? 1 : 0,
        enable_right_bar: (getCheckedValue('rightContentType') === 'bar') ? 1 : 0,
        enable_right_text: (getCheckedValue('rightContentType') === 'text') ? 1 : 0,
        right_area_mm: $('#rightAreaMm').val(),
        right_bar_mm: $('#rightBarMm').val(),
        right_bar_text_size_pt: $('#rightBarTextSizePt').val(),
        right_bar_text: $('#rightBarText').val(),
        right_text: $('#rightText').val(),
        right_bar_color: getCheckedValue('rightBarColor'),

        enable_top_area: $('#enableTopArea').is(':checked') ? 1 : 0,
        enable_top_bar: (getCheckedValue('topContentType') === 'bar') ? 1 : 0,
        enable_top_text: (getCheckedValue('topContentType') === 'text') ? 1 : 0,
        top_area_mm: $('#topAreaMm').val(),
        top_bar_mm: $('#topBarMm').val(),
        top_bar_text_size_pt: $('#topBarTextSizePt').val(),
        top_bar_text: $('#topBarText').val(),
        top_bar_color: getCheckedValue('topBarColor'),
        top_text: $('#topText').val(),
        top_divider: $('#topDivider').is(':checked') ? 1 : 0,

        enable_bottom_area: $('#enableBottomArea').is(':checked') ? 1 : 0,
        enable_bottom_bar: (getCheckedValue('bottomContentType') === 'bar') ? 1 : 0,
        enable_bottom_text: (getCheckedValue('bottomContentType') === 'text') ? 1 : 0,
        bottom_area_mm: $('#bottomAreaMm').val(),
        bottom_bar_mm: $('#bottomBarMm').val(),
        bottom_bar_text_size_pt: $('#bottomBarTextSizePt').val(),
        bottom_bar_text: $('#bottomBarText').val(),
        bottom_bar_color: getCheckedValue('bottomBarColor'),
        bottom_text: $('#bottomText').val(),
        bottom_divider: $('#bottomDivider').is(':checked') ? 1 : 0,
        bottom_show_page_numbers: $('#bottomShowPageNumbers').is(':checked') ? 1 : 0,
        bottom_page_number_mm: $('#bottomPageNumberMm').val()
    };
}

function applySettings(settings) {
    if (!settings) {
        return;
    }

    $('#labelText').val(settings.text || '');
    if (typeof settings.label_text_scroll === 'number') {
        try {
            $('#labelText')[0].setSelectionRange(settings.label_text_scroll, settings.label_text_scroll);
        } catch (e) {
            // ignore selection errors
        }
    }
    if (typeof settings.font_family !== 'undefined' && settings.font_family !== null) {
        $('#fontFamily').val(settings.font_family);
    }
    pendingFontStyle = settings.font_style || null;
    if (typeof settings.font_size !== 'undefined' && settings.font_size !== null) {
        $('#fontSize').val(settings.font_size);
    }
    if (typeof settings.label_size !== 'undefined' && settings.label_size !== null) {
        $('#labelSize').val(settings.label_size);
    }
    if (typeof settings.margin_top !== 'undefined' && settings.margin_top !== null) {
        $('#marginTop').val(settings.margin_top);
    }
    if (typeof settings.margin_bottom !== 'undefined' && settings.margin_bottom !== null) {
        $('#marginBottom').val(settings.margin_bottom);
    }
    if (typeof settings.margin_left !== 'undefined' && settings.margin_left !== null) {
        $('#marginLeft').val(settings.margin_left);
    }
    if (typeof settings.margin_right !== 'undefined' && settings.margin_right !== null) {
        $('#marginRight').val(settings.margin_right);
    }
    if (typeof settings.qrcode_size !== 'undefined' && settings.qrcode_size !== null) {
        $('#qrCodeSize').val(settings.qrcode_size);
    }
    if (typeof settings.qrcode_correction !== 'undefined' && settings.qrcode_correction !== null) {
        $('#qrCodeCorrection').val(settings.qrcode_correction);
    }
    if (typeof settings.image_bw_threshold !== 'undefined' && settings.image_bw_threshold !== null) {
        $('#imageBwThreshold').val(settings.image_bw_threshold);
    }
    $('#imageRotate90').prop('checked', settings.image_rotate_90 ? true : false);
    $('#imageStretchLength').prop('checked', settings.image_stretch_length ? true : false);
    $('#imageNoCrop').prop('checked', settings.no_crop ? true : false);
    if (typeof settings.image_crop_left !== 'undefined' && settings.image_crop_left !== null) {
        $('#imageCropLeft').val(settings.image_crop_left);
    }
    if (typeof settings.image_crop_right !== 'undefined' && settings.image_crop_right !== null) {
        $('#imageCropRight').val(settings.image_crop_right);
    }
    if (typeof settings.image_crop_top !== 'undefined' && settings.image_crop_top !== null) {
        $('#imageCropTop').val(settings.image_crop_top);
    }
    if (typeof settings.image_crop_bottom !== 'undefined' && settings.image_crop_bottom !== null) {
        $('#imageCropBottom').val(settings.image_crop_bottom);
    }
    if (typeof settings.print_count !== 'undefined' && settings.print_count !== null) {
        $('#printCount').val(settings.print_count);
    }

    if (typeof settings.markdown_slice_mm !== 'undefined' && settings.markdown_slice_mm !== null) {
        $('#markdownSliceMm').val(settings.markdown_slice_mm);
    }
    // Footer is now integrated with bottom_area_mm, no separate footer field

    $('#markdownPaged').prop('checked', settings.markdown_paged ? true : false);
    onMarkdownPagedToggle();
    $('#markdownPageNumbers').prop('checked', settings.markdown_page_numbers ? true : false);
    onMarkdownPageNumbersToggle();
    $('#markdownPageCircle').prop('checked', settings.markdown_page_circle ? true : false);
    if (typeof settings.markdown_page_number_mm !== 'undefined' && settings.markdown_page_number_mm !== null) {
        $('#markdownPageNumberMm').val(settings.markdown_page_number_mm);
    }
    $('#markdownPageCount').prop('checked', settings.markdown_page_count ? true : false);
    $('#markdownAutoRender').prop('checked', settings.markdown_auto_render ? true : false);

    // Restore border area settings
    // Left area
    if (settings.enable_left_area) {
        $('#enableLeftArea').prop('checked', true);
        toggleHeaderArea('left');
        if (typeof settings.left_area_mm !== 'undefined' && settings.left_area_mm !== null) {
            $('#leftAreaMm').val(settings.left_area_mm);
        }
        // Restore content type (bar/text/none)
        if (settings.enable_left_bar) {
            $('#leftContentBar').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('left', 'bar');
            if (typeof settings.left_bar_mm !== 'undefined' && settings.left_bar_mm !== null) {
                $('#leftBarMm').val(settings.left_bar_mm);
            }
            if (typeof settings.left_bar_text_size_pt !== 'undefined' && settings.left_bar_text_size_pt !== null) {
                $('#leftBarTextSizePt').val(settings.left_bar_text_size_pt);
            }
            if (typeof settings.left_bar_text !== 'undefined' && settings.left_bar_text !== null) {
                $('#leftBarText').val(settings.left_bar_text);
            }
            setRadioGroup('leftBarColor', settings.left_bar_color);
        } else if (settings.enable_left_text) {
            $('#leftContentText').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('left', 'text');
            if (typeof settings.left_text !== 'undefined' && settings.left_text !== null) {
                $('#leftText').val(settings.left_text);
            }
        }
    }
    // Right area
    if (settings.enable_right_area) {
        $('#enableRightArea').prop('checked', true);
        toggleHeaderArea('right');
        if (typeof settings.right_area_mm !== 'undefined' && settings.right_area_mm !== null) {
            $('#rightAreaMm').val(settings.right_area_mm);
        }
        // Restore content type (bar/text/none)
        if (settings.enable_right_bar) {
            $('#rightContentBar').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('right', 'bar');
            if (typeof settings.right_bar_mm !== 'undefined' && settings.right_bar_mm !== null) {
                $('#rightBarMm').val(settings.right_bar_mm);
            }
            if (typeof settings.right_bar_text_size_pt !== 'undefined' && settings.right_bar_text_size_pt !== null) {
                $('#rightBarTextSizePt').val(settings.right_bar_text_size_pt);
            }
            if (typeof settings.right_bar_text !== 'undefined' && settings.right_bar_text !== null) {
                $('#rightBarText').val(settings.right_bar_text);
            }
            setRadioGroup('rightBarColor', settings.right_bar_color);
        } else if (settings.enable_right_text) {
            $('#rightContentText').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('right', 'text');
            if (typeof settings.right_text !== 'undefined' && settings.right_text !== null) {
                $('#rightText').val(settings.right_text);
            }
        }
    }
    // Top area
    if (settings.enable_top_area) {
        $('#enableTopArea').prop('checked', true);
        toggleHeaderArea('top');
        if (typeof settings.top_area_mm !== 'undefined' && settings.top_area_mm !== null) {
            $('#topAreaMm').val(settings.top_area_mm);
        }
        // Restore content type (bar/text/none)
        if (settings.enable_top_bar) {
            $('#topContentBar').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('top', 'bar');
            if (typeof settings.top_bar_mm !== 'undefined' && settings.top_bar_mm !== null) {
                $('#topBarMm').val(settings.top_bar_mm);
            }
            if (typeof settings.top_bar_text_size_pt !== 'undefined' && settings.top_bar_text_size_pt !== null) {
                $('#topBarTextSizePt').val(settings.top_bar_text_size_pt);
            }
            if (typeof settings.top_bar_text !== 'undefined' && settings.top_bar_text !== null) {
                $('#topBarText').val(settings.top_bar_text);
            }
            setRadioGroup('topBarColor', settings.top_bar_color);
        } else if (settings.enable_top_text) {
            $('#topContentText').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('top', 'text');
            if (typeof settings.top_text !== 'undefined' && settings.top_text !== null) {
                $('#topText').val(settings.top_text);
            }
            $('#topDivider').prop('checked', settings.top_divider ? true : false);
        }
    }
    // Bottom area
    if (settings.enable_bottom_area) {
        $('#enableBottomArea').prop('checked', true);
        toggleHeaderArea('bottom');
        if (typeof settings.bottom_area_mm !== 'undefined' && settings.bottom_area_mm !== null) {
            $('#bottomAreaMm').val(settings.bottom_area_mm);
        }
        // Restore content type (bar/text/none)
        if (settings.enable_bottom_bar) {
            $('#bottomContentBar').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('bottom', 'bar');
            if (typeof settings.bottom_bar_mm !== 'undefined' && settings.bottom_bar_mm !== null) {
                $('#bottomBarMm').val(settings.bottom_bar_mm);
            }
            if (typeof settings.bottom_bar_text_size_pt !== 'undefined' && settings.bottom_bar_text_size_pt !== null) {
                $('#bottomBarTextSizePt').val(settings.bottom_bar_text_size_pt);
            }
            if (typeof settings.bottom_bar_text !== 'undefined' && settings.bottom_bar_text !== null) {
                $('#bottomBarText').val(settings.bottom_bar_text);
            }
            setRadioGroup('bottomBarColor', settings.bottom_bar_color);
        } else if (settings.enable_bottom_text) {
            $('#bottomContentText').addClass('active').find('input').prop('checked', true);
            toggleBorderContentType('bottom', 'text');
            if (typeof settings.bottom_text !== 'undefined' && settings.bottom_text !== null) {
                $('#bottomText').val(settings.bottom_text);
            }
            $('#bottomDivider').prop('checked', settings.bottom_divider ? true : false);
            $('#bottomShowPageNumbers').prop('checked', settings.bottom_show_page_numbers ? true : false);
            if (typeof settings.bottom_page_number_mm !== 'undefined' && settings.bottom_page_number_mm !== null) {
                $('#bottomPageNumberMm').val(settings.bottom_page_number_mm);
            }
            onBottomPageNumbersToggle();
        }
    }

    setRadioGroup('fontAlign', settings.align);
    setRadioGroup('orientation', settings.orientation);
    setRadioGroup('printType', settings.print_type);
    setRadioGroup('imageMode', settings.image_mode);
    setRadioGroup('lineSpacing', settings.line_spacing);
    {% if red_support %}
    setRadioGroup('printColor', settings.print_color);
    {% endif %}

    if (settings.print_type === 'markdown') {
        toggleMarkdownButton(true);
    }
    toggleMarkdownOptions(settings.print_type === 'markdown');
}

function detectStorageSupport() {
    try {
        var testKey = '__bql_storage_test__';
        window.localStorage.setItem(testKey, '1');
        window.localStorage.removeItem(testKey);
        return true;
    } catch (e) {
        return false;
    }
}

function loadSettings() {
    if (!storageAvailable) {
        return null;
    }
    try {
        var raw = window.localStorage.getItem(STORAGE_KEY);
        if (!raw) {
            return null;
        }
        var parsed = JSON.parse(raw);
        if (!parsed || parsed.version !== STORAGE_VERSION) {
            return null;
        }
        return parsed.settings || null;
    } catch (e) {
        return null;
    }
}

function persistSettings() {
    if (!storageReady || !storageAvailable) {
        return;
    }
    try {
        var payload = {
            version: STORAGE_VERSION,
            settings: collectSettings()
        };
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch (e) {
        // ignore quota errors
    }
}

function schedulePersist() {
    if (!storageReady || !storageAvailable || initializing) {
        return;
    }
    if (persistTimer) {
        clearTimeout(persistTimer);
    }
    persistTimer = setTimeout(persistSettings, 200);
}

function registerStorageHandlers() {
    var changeSelectors = [
        '#labelSize',
        '#fontFamily',
        '#fontStyle',
        '#fontSize',
        '#qrCodeSize',
        '#qrCodeCorrection',
        '#imageBwThreshold',
        '#printCount',
        '#marginTop',
        '#marginBottom',
        '#marginLeft',
        '#marginRight',
        '#markdownAutoRender',
        '#markdownSliceMm',
        '#markdownFooterMm',
        '#markdownPageNumberMm'
    ];

    $(changeSelectors.join(',')).on('change', schedulePersist);
    $('#labelText').on('input', schedulePersist);

    $('input[name=fontAlign]')
        .on('change', schedulePersist);
    $('input[name=orientation]')
        .on('change', schedulePersist);
    $('input[name=printType]')
        .on('change', schedulePersist);
    $('input[name=imageMode]')
        .on('change', schedulePersist);
    $('input[name=lineSpacing]')
        .on('change', schedulePersist);
    {% if red_support %}
    $('input[name=printColor]')
        .on('change', schedulePersist);
    {% endif %}
    $('#markdownPaged').on('change', schedulePersist);
    $('#markdownPageNumbers').on('change', schedulePersist);
    $('#markdownPageCircle').on('change', schedulePersist);
    $('#markdownPageCount').on('change', schedulePersist);

    var borderAreaSelectors = [
        '#enableLeftArea', '#enableLeftBar', '#enableLeftText',
        '#enableRightArea', '#enableRightBar', '#enableRightText',
        '#enableTopArea', '#enableTopBar', '#enableTopText',
        '#enableBottomArea', '#enableBottomBar', '#enableBottomText',
        '#leftAreaMm', '#leftBarMm', '#leftBarTextSizePt',
        '#rightAreaMm', '#rightBarMm', '#rightBarTextSizePt',
        '#topAreaMm', '#topBarMm', '#topBarTextSizePt', '#topDivider',
        '#bottomAreaMm', '#bottomBarMm', '#bottomBarTextSizePt', '#bottomDivider',
        '#bottomShowPageNumbers', '#bottomPageNumberMm'
    ];
    $(borderAreaSelectors.join(',')).on('change', schedulePersist);

    $('#leftText, #leftBarText, #rightText, #rightBarText, #topText, #topBarText, #bottomText, #bottomBarText').on('input', schedulePersist);
    $('input[name=leftBarColor], input[name=rightBarColor], input[name=topBarColor], input[name=bottomBarColor]').on('change', schedulePersist);
}

function loadPrinters() {
    $.get('/labeldesigner/api/printers', function(data) {
        var select = $('#printerSelect');
        select.empty();

        if (!data.printers || data.printers.length === 0) {
            select.append('<option value="">No printers configured</option>');
            return;
        }

        data.printers.forEach(function(printer) {
            var option = $('<option></option>')
                .val(printer.id)
                .text(printer.name + (printer.type === 'remote' ? ' (Remote)' : ''));
            if (printer.default) {
                option.prop('selected', true);
            }
            select.append(option);
        });

        // After loading printers, query status for auto-detection
        queryPrinterStatusAndApply();
    }).fail(function() {
        $('#printerSelect').html('<option value="">Error loading printers</option>');
    });
}

function queryPrinterStatusAndApply() {
    /**
     * Query printer status and auto-set label size/color with fallback chain:
     * 1. Try auto-detect from printer (if supported)
     * 2. Use last selected from localStorage
     * 3. Use server-configured defaults
     */
    var printerId = $('#printerSelect').val();
    if (!printerId) {
        // No printer selected, use fallback
        applyLabelSettingsFromLocalStorage();
        return;
    }

    // Check if we've already determined this printer doesn't support status
    var printerStatusCache = localStorage.getItem('printer_status_support_' + printerId);
    if (printerStatusCache === 'false') {
        // Skip query, use fallback
        applyLabelSettingsFromLocalStorage();
        return;
    }

    // Query printer status
    $.get('/labeldesigner/api/printer/status?printer_id=' + printerId)
        .done(function(response) {
            if (response.success && response.status) {
                var status = response.status;

                // Cache that this printer supports status
                localStorage.setItem('printer_status_support_' + printerId, 'true');

                // Auto-set label size if detected
                if (status.media_type) {
                    var labelSelect = $('#labelSize');
                    var option = labelSelect.find('option[value="' + status.media_type + '"]');
                    if (option.length > 0) {
                        labelSelect.val(status.media_type);
                        console.log('Auto-detected label size: ' + status.media_type);

                        // Save to localStorage for next time
                        localStorage.setItem('last_label_size', status.media_type);
                    }
                }

                // Trigger preview update
                preview();
            } else {
                // Status query not supported or failed
                if (response.supported === false) {
                    // Cache that this printer doesn't support status
                    localStorage.setItem('printer_status_support_' + printerId, 'false');
                }
                // Fall back to localStorage
                applyLabelSettingsFromLocalStorage();
            }
        })
        .fail(function() {
            // Query failed, fall back to localStorage
            applyLabelSettingsFromLocalStorage();
        });
}

function applyLabelSettingsFromLocalStorage() {
    /**
     * Apply last-used label size from localStorage as fallback
     */
    var lastLabelSize = localStorage.getItem('last_label_size');
    if (lastLabelSize) {
        var labelSelect = $('#labelSize');
        var option = labelSelect.find('option[value="' + lastLabelSize + '"]');
        if (option.length > 0) {
            labelSelect.val(lastLabelSize);
            console.log('Applied last-used label size from localStorage: ' + lastLabelSize);
        }
    }
    // If no localStorage value, server defaults are already set
}

$(function() {
    storageAvailable = detectStorageSupport();
    var settings = loadSettings();
    if (settings) {
        applySettings(settings);
    }

    toggleMarkdownOptions(getCheckedValue('printType') === 'markdown');
    updateOrientationRestrictions();

    // Initialize markdown button states
    if ($('#markdownAutoRender').is(':checked')) {
        $('#markdownAutoRenderBtn').addClass('active');
    }
    if ($('#markdownPaged').is(':checked')) {
        $('#markdownPagedBtn').addClass('active');
        $('#markdownPagedInputs').show();
    }

    registerStorageHandlers();
    updateStyles();
    loadPrinters();

    // Clear preview if in image mode but no file loaded
    var printType = getCheckedValue('printType');
    if (printType === 'image' && !uploadedImageFile) {
        $('#previewImg').attr('src', '');
        // Also clear any markdown state that might be lingering
        clearMarkdownPreviewState();
    } else if (printType !== 'image') {
        // Only generate preview for text-based modes
        preview();
    }

    // Save label size to localStorage when changed manually
    $('#labelSize').on('change', function() {
        var labelSize = $(this).val();
        if (labelSize) {
            localStorage.setItem('last_label_size', labelSize);
        }
    });

    // Re-query status when printer is changed
    $('#printerSelect').on('change', function() {
        queryPrinterStatusAndApply();
    });

    $('#pageRangeClear').on('click', function() {
        clearPageRangeInputs();
        if (isPdfLoaded) {
            normalizePageRange(pdfTotalPages);
        } else if (markdownPreviewPages.length > 0) {
            normalizePageRange(markdownPreviewPages.length);
        }
    });

    $('#pageFrom, #pageTo').on('change', function() {
        var total = isPdfLoaded ? pdfTotalPages : (markdownPreviewPages.length || 0);
        if (total > 0) {
            normalizePageRange(total);
        }
        schedulePersist();
    });

    setTimeout(function() {
        initializing = false;
        storageReady = storageAvailable;
    }, 0);
});

Dropzone.options.myAwesomeDropzone = {
    url: function() {
        if (dropZoneMode == 'preview') {
            return "{{url_for('.get_preview_from_image')}}?return_format=base64";
        } else {
            return "{{url_for('.print_text')}}";
        }
    },
    paramName: "image",
    acceptedFiles: 'image/png,image/jpeg,application/pdf',
    maxFiles: 1,
    addRemoveLinks: true,
    autoProcessQueue: false,
    init: function() {
        imageDropZone = this;

        this.on("addedfile", function() {
            if (this.files[1] != null) {
                this.removeFile(this.files[0]);
            }
        });
    },

    sending: function(file, xhr, data) {
        // append all parameters to the request
        var fd = formData(false);

        $.each(fd, function(key, value){
            data.append(key, value);
        });
    },

    success: function(file, response) {
        // If preview or print was successfull update the previewpane or print status
        console.log('[dropzone] Success callback - mode:', dropZoneMode, 'file status before:', file.status);
        if (dropZoneMode == 'preview') {
            handlePreviewResponse(response, getCheckedValue('printType') === 'markdown');
        } else {
            setStatus(response);
        }
        file.status = Dropzone.QUEUED;
        console.log('[dropzone] File status set to QUEUED, files in dropzone:', imageDropZone.files.length);
    },

    accept: function(file, done) {
        // If a valid file was added, store reference and perform the preview
        uploadedImageFile = file;
        console.log('[dropzone] File accepted and stored:', file.name);
        done();
        preview();
    },

    removedfile: function(file) {
        file.previewElement.remove();
        uploadedImageFile = null;  // Clear stored file reference
        // Clear preview image completely
        $('#previewImg').attr('src', '');
        // Reset PDF navigation
        isPdfLoaded = false;
        pdfCurrentPage = 1;
        pdfTotalPages = 1;
        $('#pdfPageNavigation').hide();
        $('#pageRange').hide();
    }
};

// PDF page navigation functions
function changePdfPage(direction) {
    var newPage = pdfCurrentPage + direction;
    if (newPage >= 1 && newPage <= pdfTotalPages) {
        pdfCurrentPage = newPage;
        $('#pdfPageInput').val(pdfCurrentPage);
        updatePageButtons();
        preview();  // Reload preview with new page
    }
}

function updatePageButtons() {
    $('#pdfPrevPage').prop('disabled', pdfCurrentPage <= 1);
    $('#pdfNextPage').prop('disabled', pdfCurrentPage >= pdfTotalPages);
}

function setPdfPageInfo(currentPage, totalPages) {
    console.log('[setPdfPageInfo] Setting PDF info - current:', currentPage, 'total:', totalPages);
    isPdfLoaded = true;
    pdfCurrentPage = currentPage;
    pdfTotalPages = totalPages;
    $('#pdfTotalPages').text(totalPages);
    $('#pdfPageInput').attr('max', totalPages).val(currentPage);

    // Only set default page range values if they're not already set by the user
    var fromInput = $('#pageFrom');
    var toInput = $('#pageTo');
    if (!fromInput.val() || fromInput.val() == '') {
        fromInput.attr('max', totalPages).val(1);
    } else {
        fromInput.attr('max', totalPages);
    }
    if (!toInput.val() || toInput.val() == '') {
        toInput.attr('max', totalPages).val(totalPages);
    } else {
        toInput.attr('max', totalPages);
    }

    $('#pdfPageNavigation').show();
    $('#pageRange').show();
    // Force display to ensure it's visible
    $('#pageRange').css('display', 'block');
    console.log('[setPdfPageInfo] Showing page range, exists:', $('#pageRange').length, 'visibility:', $('#pageRange').is(':visible'), 'display style:', $('#pageRange').css('display'));
    console.log('[setPdfPageInfo] Page range parent visible:', $('#pageRange').parent().is(':visible'));
    console.log('[setPdfPageInfo] Page range offset top:', $('#pageRange').offset() ? $('#pageRange').offset().top : 'null');
    console.log('[setPdfPageInfo] Page range values - from:', $('#pageFrom').val(), 'to:', $('#pageTo').val());
    normalizePageRange(totalPages);
    updatePageButtons();
}

function goToPdfPage() {
    var pageNum = parseInt($('#pdfPageInput').val());
    if (pageNum >= 1 && pageNum <= pdfTotalPages) {
        pdfCurrentPage = pageNum;
        updatePageButtons();
        preview();
    } else {
        // Reset to current page if invalid
        $('#pdfPageInput').val(pdfCurrentPage);
    }
}
        fromInput.attr('max', totalPages);
    }
    if (!toInput.val() || toInput.val() == '') {
        toInput.attr('max', totalPages).val(totalPages);
    } else {
        toInput.attr('max', totalPages);
    }

    $('#pdfPageNavigation').show();
    $('#pageRange').show();
    // Force display to ensure it's visible
    $('#pageRange').css('display', 'block');
    console.log('[setPdfPageInfo] Showing page range, exists:', $('#pageRange').length, 'visibility:', $('#pageRange').is(':visible'), 'display style:', $('#pageRange').css('display'));
    console.log('[setPdfPageInfo] Page range parent visible:', $('#pageRange').parent().is(':visible'));
    console.log('[setPdfPageInfo] Page range offset top:', $('#pageRange').offset() ? $('#pageRange').offset().top : 'null');
    console.log('[setPdfPageInfo] Page range values - from:', $('#pageFrom').val(), 'to:', $('#pageTo').val());
    normalizePageRange(totalPages);
    updatePageButtons();
}

function goToPdfPage() {
    var pageNum = parseInt($('#pdfPageInput').val());
    if (pageNum >= 1 && pageNum <= pdfTotalPages) {
        pdfCurrentPage = pageNum;
        updatePageButtons();
        preview();
    } else {
        // Reset to current page if invalid
        $('#pdfPageInput').val(pdfCurrentPage);
    }
}
