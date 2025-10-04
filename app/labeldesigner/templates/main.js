var dropZoneMode = 'preview';
var imageDropZone;
var markdownRenderInFlight = false;
var pendingFontStyle = null;

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
    data.markdown_footer_mm = $('#markdownFooterMm').val();
    data.markdown_page_numbers = markdownPageNumbersEnabled() ? 1 : 0;
    data.markdown_page_circle = $('#markdownPageCircle').is(':checked') ? 1 : 0;
    data.markdown_page_number_mm = $('#markdownPageNumberMm').val();
    data.markdown_page_count = $('#markdownPageCount').is(':checked') ? 1 : 0;
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

    $.ajax({
        type:        'POST',
        url:         '{{url_for('.get_font_styles')}}',
        contentType: 'application/x-www-form-urlencoded; charset=UTF-8',
        data:        {font: font_family},
        success: function(data) {
            var styleSelect = $('#fontStyle');
            styleSelect.empty();

            var firstKey = null;
            $.each(data, function(key) {
                styleSelect.append($("<option></option>").attr("value", key).text(key));
                if (firstKey === null) {
                    firstKey = key;
                }
            });

            var desired = null;
            if (pendingFontStyle && Object.prototype.hasOwnProperty.call(data, pendingFontStyle)) {
                desired = pendingFontStyle;
            } else if (Object.prototype.hasOwnProperty.call(data, 'Book')) {
                desired = 'Book';
            } else if (Object.prototype.hasOwnProperty.call(data, 'Regular')) {
                desired = 'Regular';
            } else {
                desired = firstKey;
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
    if (show && markdownAutoRenderEnabled()) {
        button.hide();
        if (markdownRenderInFlight) {
            finishMarkdownRenderFeedback();
        }
        return;
    }
    if (show) {
        button.show();
    } else {
        if (markdownRenderInFlight) {
            finishMarkdownRenderFeedback();
        }
        button.hide();
    }
}

function toggleMarkdownOptions(show) {
    var container = $('#markdownOptions');
    if (!container.length) {
        return;
    }
    if (show) {
        container.show();
    } else {
        container.hide();
        $('#markdownPagedInputs').hide();
        $('#markdownPageNumberInputs').hide();
    }
    enforceMarkdownPagingRules();
    updateMarkdownPager();
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
    enforceMarkdownPagingRules();
}

function onMarkdownAutoRenderToggle() {
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
    button.html('<span class="fas fa-sync fa-spin" aria-hidden="true"></span> Rendering Markdown...');
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
    button.prop('disabled', false);
    markdownRenderInFlight = false;
}

function clearMarkdownPreviewState() {
    markdownPreviewPages = [];
    markdownCurrentPage = 0;
    updateMarkdownPager();
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

    if (typeof raw === 'object' && raw !== null && Array.isArray(raw.pages)) {
        return raw.pages;
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
    var pages = normalizePreviewPages(raw);
    if (!pages.length) {
        if (isMarkdown) {
            clearMarkdownPreviewState();
        }
        return;
    }

    if (isMarkdown) {
        var previousPage = markdownCurrentPage;
        markdownPreviewPages = pages;
        showMarkdownPage(Math.min(previousPage, pages.length - 1));
    } else {
        clearMarkdownPreviewState();
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
        clearMarkdownPreviewState();
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
    } else {
        $('#groupLabelText').show();
        $('#groupLabelImage').hide();
    }

    if (printType === 'image') {
        dropZoneMode = 'preview';
        if (imageDropZone) {
            imageDropZone.processQueue();
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
                if (markdownButtonResetTimer) {
                    clearTimeout(markdownButtonResetTimer);
                }
                finishMarkdownRenderFeedback('<span class="fas fa-exclamation-triangle" aria-hidden="true"></span> Render Failed');
                markdownButtonResetTimer = setTimeout(function() {
                    var btn = $('#renderMarkdownButton');
                    if (btn.length && markdownButtonDefaultHtml) {
                        btn.html(markdownButtonDefaultHtml);
                    }
                }, 4000);
                console.warn('Markdown preview failed:', msg);
                clearMarkdownPreviewState();
            }
        }
    }).always(function() {
        if (isMarkdown && force && markdownRenderInFlight) {
            if (markdownButtonResetTimer) {
                clearTimeout(markdownButtonResetTimer);
                markdownButtonResetTimer = null;
            }
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
        print_count: $('#printCount').val(),
        line_spacing: getCheckedValue('lineSpacing'),
        {% if red_support %}
        print_color: getCheckedValue('printColor'),
        {% endif %}
        label_text_scroll: caret,
        markdown_auto_render: markdownAutoRenderEnabled() ? 1 : 0,
        markdown_paged: markdownPagedEnabled() ? 1 : 0,
        markdown_slice_mm: $('#markdownSliceMm').val(),
        markdown_footer_mm: normalizeFooterInput(),
        markdown_page_numbers: markdownPageNumbersEnabled() ? 1 : 0,
        markdown_page_circle: $('#markdownPageCircle').is(':checked') ? 1 : 0,
        markdown_page_number_mm: $('#markdownPageNumberMm').val(),
        markdown_page_count: $('#markdownPageCount').is(':checked') ? 1 : 0
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
    if (typeof settings.print_count !== 'undefined' && settings.print_count !== null) {
        $('#printCount').val(settings.print_count);
    }

    if (typeof settings.markdown_slice_mm !== 'undefined' && settings.markdown_slice_mm !== null) {
        $('#markdownSliceMm').val(settings.markdown_slice_mm);
    }
    if (typeof settings.markdown_footer_mm !== 'undefined' && settings.markdown_footer_mm !== null) {
        var footerValue = parseFloat(settings.markdown_footer_mm);
        if (isNaN(footerValue)) {
            footerValue = 0;
        }
        $('#markdownFooterMm').val(Math.max(0, footerValue));
    } else {
        $('#markdownFooterMm').val(4);
    }

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
    }).fail(function() {
        $('#printerSelect').html('<option value="">Error loading printers</option>');
    });
}

$(function() {
    storageAvailable = detectStorageSupport();
    var settings = loadSettings();
    if (settings) {
        applySettings(settings);
    }

    toggleMarkdownOptions(getCheckedValue('printType') === 'markdown');

    registerStorageHandlers();
    updateStyles();
    loadPrinters();
    preview();

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
        if (dropZoneMode == 'preview') {
            handlePreviewResponse(response, getCheckedValue('printType') === 'markdown');
        } else {
            setStatus(response);
        }
        file.status = Dropzone.QUEUED;
    },

    accept: function(file, done) {
        // If a valid file was added, perform the preview
        done();
        preview();
    },

    removedfile: function(file) {
        file.previewElement.remove();
        preview();
        // Insert a dummy image
        updatePreview('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=');
    }
};
