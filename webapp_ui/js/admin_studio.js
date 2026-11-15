// ================================================================
// webapp_ui/js/admin_studio.js — استوديو تحديد الأختام والخطوط للموبايل
// ================================================================

(function () {
  const TYPE_LABELS = {
    name: "👤 الاسم",
    phone: "📱 الهاتف",
    doc_name: "📚 السلسلة",
    watermark: "💧 مائية",
    logo_watermark: "🖼️ لوجو"
  };

  const TYPE_COLORS = {
    name: "#38bdf8",
    phone: "#34d399",
    doc_name: "#c084fc",
    watermark: "#fbbf24",
    logo_watermark: "#f472b6"
  };

  const state = {
    initialized: false,
    materials: [],
    fonts: [],
    materialId: null,
    totalPages: 1,
    currentPage: 1,
    pageFramePath: null,
    elements: [],
    selectedIdx: -1,
    mode: "drag", // "drag" | "live"
    lastBlobUrl: null,
    draggingIdx: -1,
    dragPointerId: null
  };

  function toast(msg) {
    if (window.ZPactNativeBridge && typeof window.ZPactNativeBridge.showToast === "function") {
      try { window.ZPactNativeBridge.showToast(msg); } catch (_) {}
    }
    const container = document.getElementById("toast_container");
    if (!container) return;
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = msg;
    container.appendChild(el);
    setTimeout(() => {
      el.style.opacity = "0";
      setTimeout(() => el.remove(), 300);
    }, 2800);
  }

  function qs(id) {
    return document.getElementById(id);
  }

  function clamp(val, min, max) {
    return Math.min(max, Math.max(min, val));
  }

  function defaultElementsList(fontFamily = "cairo") {
    return [
      { type: "name", x_pct: 50, y_pct: 4.5, font_size: 16, font_family: fontFamily, color: "#0D47A1", opacity: 1.0, rotation: 0, page: null, excluded_pages: [], z_index_mode: "over", pattern: "single" },
      { type: "phone", x_pct: 50, y_pct: 95.5, font_size: 14, font_family: fontFamily, color: "#1E293B", opacity: 1.0, rotation: 0, page: null, excluded_pages: [], z_index_mode: "over", pattern: "single" },
      { type: "doc_name", x_pct: 16, y_pct: 95.5, font_size: 13, font_family: fontFamily, color: "#0D47A1", opacity: 0.95, rotation: 0, page: null, excluded_pages: [], z_index_mode: "over", pattern: "single" },
      { type: "watermark", x_pct: 50, y_pct: 50, font_size: 38, font_family: fontFamily, color: "#1565C0", opacity: 0.14, rotation: -35, page: null, excluded_pages: [1], z_index_mode: "over", pattern: "diagonal_watermark" }
    ];
  }

  function isElementVisibleOnPage(el, pageNum) {
    if (el.page !== null && el.page !== undefined && Number(el.page) !== Number(pageNum)) {
      return false;
    }
    if (Array.isArray(el.excluded_pages) && el.excluded_pages.map(Number).includes(Number(pageNum))) {
      return false;
    }
    return true;
  }

  function populateFontSelect() {
    const sel = qs("studio_prop_font");
    if (!sel) return;
    const list = state.fonts.length
      ? state.fonts
      : [
          { key: "cairo", label: "Cairo Bold (الافتراضي)" },
          { key: "tahoma", label: "Tahoma" },
          { key: "arial", label: "Arial" },
          { key: "simplified", label: "Simplified Arabic" },
          { key: "traditional", label: "Traditional Arabic" },
          { key: "arabtype", label: "Arabic Typesetting" },
          { key: "naskh", label: "Amiri / Naskh" },
          { key: "times", label: "Times New Roman" }
        ];
    sel.innerHTML = list.map(f => `<option value="${f.key}">${f.label}</option>`).join("");
  }

  function updateFrameBadge() {
    const delBtn = qs("studio_btn_delete_frame");
    const status = qs("studio_frame_status");
    const hasFrame = Boolean(state.pageFramePath);
    if (delBtn) delBtn.style.display = hasFrame ? "inline-flex" : "none";
    if (status) {
      status.textContent = hasFrame ? "🖼️ إطار مفعّل على الصفحات" : "بدون إطار حالياً";
      status.style.color = hasFrame ? "#34d399" : "var(--text-secondary)";
    }
  }

  function setLoading(show) {
    const badge = qs("studio_loading_badge");
    if (badge) badge.style.display = show ? "block" : "none";
  }

  async function renderCanvasImage() {
    if (!state.materialId) return;
    const imgEl = qs("studio_page_img");
    if (!imgEl) return;

    setLoading(true);
    let url = null;
    if (state.mode === "live") {
      url = await window.apiService.renderLivePreviewBlobUrl(state.materialId, {
        page: state.currentPage,
        zoom: 1.35,
        stamp_elements: state.elements,
        buyer_name: "أ/ نموذج تجريبي",
        buyer_phone: "01012345678",
        doc_name_stamp: "سلسلة التفوق",
        watermark_text: "Z-PACT",
        watermark_type: "text",
        apply_stamps: true
      });
    } else {
      url = await window.apiService.fetchPreviewPageBlobUrl(state.materialId, state.currentPage, 0, 1.35);
    }
    setLoading(false);

    if (url) {
      if (state.lastBlobUrl) URL.revokeObjectURL(state.lastBlobUrl);
      state.lastBlobUrl = url;
      imgEl.src = url;
    } else {
      toast("⚠️ تعذر تحميل صورة المعاينة لهذه الصفحة");
    }
  }

  function renderChips() {
    const overlay = qs("studio_overlay");
    const strip = qs("studio_elements_strip");
    if (!overlay || !strip) return;

    overlay.style.display = state.mode === "live" ? "none" : "block";
    overlay.innerHTML = "";
    strip.innerHTML = "";

    state.elements.forEach((el, idx) => {
      const label = TYPE_LABELS[el.type] || el.type;
      const color = TYPE_COLORS[el.type] || "#38bdf8";
      const visible = isElementVisibleOnPage(el, state.currentPage);

      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "studio-el-pill" + (idx === state.selectedIdx ? " active" : "");
      pill.textContent = `${label} (${Number(el.font_size || 14)}pt)`;
      pill.onclick = () => selectElement(idx);
      strip.appendChild(pill);

      const chip = document.createElement("div");
      chip.className = "studio-chip" + (idx === state.selectedIdx ? " active" : "") + (visible ? "" : " other-page");
      chip.dataset.idx = String(idx);
      chip.style.left = `${clamp(Number(el.x_pct ?? 50), -5, 105)}%`;
      chip.style.top = `${clamp(Number(el.y_pct ?? 50), -5, 105)}%`;
      chip.style.borderColor = idx === state.selectedIdx ? "var(--neon-cyan)" : color;
      chip.textContent = `${label} • ${Math.round(el.font_size || 14)}pt`;
      overlay.appendChild(chip);
    });
  }

  function syncInspector() {
    const inspector = qs("studio_inspector");
    if (!inspector) return;
    if (state.selectedIdx < 0 || state.selectedIdx >= state.elements.length) {
      inspector.style.display = "none";
      return;
    }

    const el = state.elements[state.selectedIdx];
    inspector.style.display = "block";

    const title = qs("studio_sel_title");
    if (title) title.textContent = `خصائص: ${TYPE_LABELS[el.type] || el.type}`;

    const xy = qs("studio_xy_readout");
    if (xy) xy.textContent = `${Number(el.x_pct).toFixed(1)}% , ${Number(el.y_pct).toFixed(1)}%`;

    const fontSel = qs("studio_prop_font");
    if (fontSel) fontSel.value = el.font_family || "cairo";

    const sizeRange = qs("studio_prop_size");
    const sizeVal = qs("studio_prop_size_val");
    const fs = Number(el.font_size || 14);
    if (sizeRange) sizeRange.value = String(clamp(fs, 6, 120));
    if (sizeVal) sizeVal.textContent = String(fs);

    const colorInput = qs("studio_prop_color");
    if (colorInput) colorInput.value = (el.color && String(el.color).startsWith("#")) ? el.color : "#0D47A1";

    const opRange = qs("studio_prop_opacity");
    const opVal = qs("studio_prop_opacity_val");
    const op = Number(el.opacity ?? 1.0);
    if (opRange) opRange.value = String(op);
    if (opVal) opVal.textContent = `${Math.round(op * 100)}%`;

    const scopeSel = qs("studio_prop_scope");
    if (scopeSel) {
      if (el.page !== null && el.page !== undefined) {
        scopeSel.value = "current_only";
      } else if (Array.isArray(el.excluded_pages) && el.excluded_pages.includes(1)) {
        scopeSel.value = "no_cover";
      } else {
        scopeSel.value = "all";
      }
    }

    const patSel = qs("studio_prop_pattern");
    if (patSel) patSel.value = el.pattern || "single";

    const rotInp = qs("studio_prop_rot");
    if (rotInp) rotInp.value = String(Number(el.rotation || 0));
  }

  function selectElement(idx) {
    state.selectedIdx = idx;
    renderChips();
    syncInspector();
  }

  function updatePageLabel() {
    const lbl = qs("studio_page_label");
    if (lbl) lbl.textContent = `صفحة ${state.currentPage} / ${state.totalPages}`;
  }

  async function loadMaterialIntoStudio(materialId) {
    if (!materialId) return;
    state.materialId = Number(materialId);
    setLoading(true);
    const data = await window.apiService.getMaterialLayout(state.materialId);
    setLoading(false);
    if (!data) {
      toast("⚠️ تعذر تحميل بيانات هذه المذكرة");
      return;
    }

    if (Array.isArray(data.fonts) && data.fonts.length) {
      state.fonts = data.fonts;
      populateFontSelect();
    }

    state.totalPages = Math.max(1, Number(data.material?.total_pages || 1));
    state.currentPage = 1;
    state.pageFramePath = data.material?.page_frame_path || null;
    state.elements = Array.isArray(data.stamp_elements) && data.stamp_elements.length
      ? data.stamp_elements.map(x => ({ font_family: "cairo", ...x }))
      : defaultElementsList();

    state.selectedIdx = state.elements.length ? 0 : -1;
    updatePageLabel();
    updateFrameBadge();
    renderChips();
    syncInspector();
    await renderCanvasImage();
  }

  async function loadMaterialOptions(preferMatId = null) {
    const sel = qs("studio_mat_select");
    if (!sel) return;
    const [matRes, fontRes] = await Promise.all([
      window.apiService.getAdminMaterials(true),
      window.apiService.getFonts()
    ]);

    if (fontRes && Array.isArray(fontRes.fonts)) {
      state.fonts = fontRes.fonts;
      populateFontSelect();
    }

    state.materials = (matRes && matRes.materials) || [];
    if (!state.materials.length) {
      sel.innerHTML = '<option value="">لا توجد مذكرات مرفوعة بعد</option>';
      return;
    }

    sel.innerHTML = state.materials.map(m => {
      const pubMark = m.is_published === false ? " [مخفية]" : "";
      const frmMark = m.page_frame_path ? " 🖼️" : "";
      return `<option value="${m.id}">#${m.id} — ${m.title} (${m.total_pages || 1} ص)${frmMark}${pubMark}</option>`;
    }).join("");

    const targetId = preferMatId && state.materials.some(m => String(m.id) === String(preferMatId))
      ? String(preferMatId)
      : (state.materialId && state.materials.some(m => m.id === state.materialId)
        ? String(state.materialId)
        : String(state.materials[0].id));

    sel.value = targetId;
    await loadMaterialIntoStudio(targetId);
  }

  function setupPointerDrag() {
    const overlay = qs("studio_overlay");
    if (!overlay) return;

    function updatePositionFromEvent(clientX, clientY) {
      if (state.draggingIdx < 0 || !state.elements[state.draggingIdx]) return;
      const rect = overlay.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      const xPct = clamp(((clientX - rect.left) / rect.width) * 100, -15, 115);
      const yPct = clamp(((clientY - rect.top) / rect.height) * 100, -15, 115);
      const el = state.elements[state.draggingIdx];
      el.x_pct = Math.round(xPct * 10) / 10;
      el.y_pct = Math.round(yPct * 10) / 10;

      const chip = overlay.querySelector(`.studio-chip[data-idx="${state.draggingIdx}"]`);
      if (chip) {
        chip.style.left = `${el.x_pct}%`;
        chip.style.top = `${el.y_pct}%`;
      }
      const xy = qs("studio_xy_readout");
      if (xy) xy.textContent = `${el.x_pct.toFixed(1)}% , ${el.y_pct.toFixed(1)}%`;
    }

    overlay.addEventListener("pointerdown", (e) => {
      const chip = e.target.closest(".studio-chip");
      if (!chip) return;
      const idx = Number(chip.dataset.idx);
      if (Number.isNaN(idx)) return;
      e.preventDefault();
      state.draggingIdx = idx;
      state.dragPointerId = e.pointerId;
      selectElement(idx);
      try { overlay.setPointerCapture(e.pointerId); } catch (_) {}
    });

    overlay.addEventListener("pointermove", (e) => {
      if (state.draggingIdx < 0 || e.pointerId !== state.dragPointerId) return;
      e.preventDefault();
      updatePositionFromEvent(e.clientX, e.clientY);
    });

    function endDrag(e) {
      if (state.draggingIdx < 0) return;
      state.draggingIdx = -1;
      state.dragPointerId = null;
      try { overlay.releasePointerCapture(e.pointerId); } catch (_) {}
      syncInspector();
    }

    overlay.addEventListener("pointerup", endDrag);
    overlay.addEventListener("pointercancel", endDrag);
  }

  function bindControls() {
    qs("studio_mat_select")?.addEventListener("change", (e) => {
      if (e.target.value) loadMaterialIntoStudio(e.target.value);
    });

    qs("studio_btn_reload")?.addEventListener("click", () => {
      loadMaterialOptions(state.materialId);
    });

    qs("studio_btn_prev_page")?.addEventListener("click", () => {
      if (state.currentPage > 1) {
        state.currentPage -= 1;
        updatePageLabel();
        renderChips();
        renderCanvasImage();
      }
    });

    qs("studio_btn_next_page")?.addEventListener("click", () => {
      if (state.currentPage < state.totalPages) {
        state.currentPage += 1;
        updatePageLabel();
        renderChips();
        renderCanvasImage();
      }
    });

    qs("studio_mode_drag")?.addEventListener("click", () => {
      state.mode = "drag";
      qs("studio_mode_drag").classList.add("active");
      qs("studio_mode_live").classList.remove("active");
      renderChips();
      renderCanvasImage();
    });

    qs("studio_mode_live")?.addEventListener("click", () => {
      state.mode = "live";
      qs("studio_mode_live").classList.add("active");
      qs("studio_mode_drag").classList.remove("active");
      renderChips();
      renderCanvasImage();
    });

    // Frame upload & delete
    qs("studio_btn_upload_frame")?.addEventListener("click", () => {
      if (!state.materialId) return toast("⚠️ اختر المذكرة أولاً");
      qs("studio_frame_file")?.click();
    });

    qs("studio_frame_file")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file || !state.materialId) return;
      toast("⏳ جاري معالجة الإطار وتحويل الخلفية لشفافة...");
      const res = await window.apiService.uploadMaterialFrame(state.materialId, file);
      if (res && !res.error) {
        state.pageFramePath = res.page_frame_path;
        updateFrameBadge();
        toast("✅ تم تركيب الإطار الشفاف على المذكرة!");
        await renderCanvasImage();
      } else {
        toast("❌ فشل رفع الإطار: " + (res?.error || "خطأ غير معروف"));
      }
    });

    qs("studio_btn_delete_frame")?.addEventListener("click", async () => {
      if (!state.materialId) return;
      const res = await window.apiService.deleteMaterialFrame(state.materialId);
      if (res && !res.error) {
        state.pageFramePath = null;
        updateFrameBadge();
        toast("🗑️ تم إزالة الإطار من المذكرة");
        await renderCanvasImage();
      }
    });

    // Add / Delete Elements
    document.querySelectorAll(".studio-add-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const type = btn.dataset.type || "name";
        const baseFont = state.elements[0]?.font_family || "cairo";
        const newEl = {
          type,
          x_pct: 50,
          y_pct: type === "watermark" ? 50 : 12,
          font_size: type === "watermark" ? 36 : 14,
          font_family: baseFont,
          color: "#0D47A1",
          opacity: type === "watermark" ? 0.16 : 1.0,
          rotation: type === "watermark" ? -35 : 0,
          page: null,
          excluded_pages: type === "watermark" ? [1] : [],
          z_index_mode: "over",
          pattern: type === "watermark" ? "diagonal_watermark" : "single"
        };
        state.elements.push(newEl);
        if (state.mode === "live") {
          qs("studio_mode_drag")?.click();
        }
        selectElement(state.elements.length - 1);
      });
    });

    qs("studio_btn_delete_el")?.addEventListener("click", () => {
      if (state.selectedIdx < 0) return;
      if (state.elements.length <= 1) {
        return toast("⚠️ يجب أن يبقى عنصر واحد على الأقل");
      }
      state.elements.splice(state.selectedIdx, 1);
      state.selectedIdx = Math.min(state.selectedIdx, state.elements.length - 1);
      renderChips();
      syncInspector();
      if (state.mode === "live") renderCanvasImage();
    });

    // D-Pad Nudge
    document.querySelectorAll(".studio-dpad button[data-dx]").forEach(btn => {
      btn.addEventListener("click", () => {
        if (state.selectedIdx < 0) return;
        const step = Number(qs("studio_nudge_step")?.value || 1.5);
        const dx = Number(btn.dataset.dx || 0) * step;
        const dy = Number(btn.dataset.dy || 0) * step;
        const el = state.elements[state.selectedIdx];
        el.x_pct = Math.round(clamp(Number(el.x_pct) + dx, -50, 150) * 10) / 10;
        el.y_pct = Math.round(clamp(Number(el.y_pct) + dy, -50, 150) * 10) / 10;
        renderChips();
        syncInspector();
        if (state.mode === "live") renderCanvasImage();
      });
    });

    qs("studio_btn_center_el")?.addEventListener("click", () => {
      if (state.selectedIdx < 0) return;
      state.elements[state.selectedIdx].x_pct = 50;
      renderChips();
      syncInspector();
      if (state.mode === "live") renderCanvasImage();
    });

    // Property controls
    qs("studio_prop_font")?.addEventListener("change", (e) => {
      if (state.selectedIdx < 0) return;
      state.elements[state.selectedIdx].font_family = e.target.value;
      if (state.mode === "live") renderCanvasImage();
    });

    function setSelectedFontSize(newSize) {
      if (state.selectedIdx < 0) return;
      const clamped = clamp(Math.round(Number(newSize) || 14), 6, 180);
      state.elements[state.selectedIdx].font_size = clamped;
      if (qs("studio_prop_size")) qs("studio_prop_size").value = String(clamp(clamped, 6, 120));
      if (qs("studio_prop_size_val")) qs("studio_prop_size_val").textContent = String(clamped);
      renderChips();
      if (state.mode === "live") renderCanvasImage();
    }

    qs("studio_prop_size")?.addEventListener("input", (e) => {
      setSelectedFontSize(e.target.value);
    });

    qs("studio_btn_font_minus")?.addEventListener("click", () => {
      if (state.selectedIdx < 0) return;
      setSelectedFontSize((state.elements[state.selectedIdx].font_size || 14) - 1);
    });

    qs("studio_btn_font_plus")?.addEventListener("click", () => {
      if (state.selectedIdx < 0) return;
      setSelectedFontSize((state.elements[state.selectedIdx].font_size || 14) + 1);
    });

    qs("studio_prop_color")?.addEventListener("input", (e) => {
      if (state.selectedIdx < 0) return;
      state.elements[state.selectedIdx].color = e.target.value;
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_prop_opacity")?.addEventListener("input", (e) => {
      if (state.selectedIdx < 0) return;
      const val = clamp(Number(e.target.value), 0.05, 1.0);
      state.elements[state.selectedIdx].opacity = val;
      if (qs("studio_prop_opacity_val")) qs("studio_prop_opacity_val").textContent = `${Math.round(val * 100)}%`;
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_prop_scope")?.addEventListener("change", (e) => {
      if (state.selectedIdx < 0) return;
      const el = state.elements[state.selectedIdx];
      if (e.target.value === "current_only") {
        el.page = state.currentPage;
        el.excluded_pages = [];
      } else if (e.target.value === "no_cover") {
        el.page = null;
        el.excluded_pages = [1];
      } else {
        el.page = null;
        el.excluded_pages = [];
      }
      renderChips();
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_prop_pattern")?.addEventListener("change", (e) => {
      if (state.selectedIdx < 0) return;
      state.elements[state.selectedIdx].pattern = e.target.value;
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_prop_rot")?.addEventListener("change", (e) => {
      if (state.selectedIdx < 0) return;
      state.elements[state.selectedIdx].rotation = Number(e.target.value || 0);
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_btn_apply_font_all")?.addEventListener("click", () => {
      if (state.selectedIdx < 0) return;
      const font = state.elements[state.selectedIdx].font_family || "cairo";
      state.elements.forEach(el => { el.font_family = font; });
      toast(`🔤 تم تعميم الخط (${font}) على جميع العناصر`);
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_btn_reset")?.addEventListener("click", () => {
      state.elements = defaultElementsList();
      selectElement(0);
      toast("↺ تم استرجاع التنسيق الافتراضي (اضغط حفظ لتثبيته)");
      if (state.mode === "live") renderCanvasImage();
    });

    qs("studio_btn_save")?.addEventListener("click", async () => {
      if (!state.materialId) return toast("⚠️ اختر المذكرة أولاً");
      const btn = qs("studio_btn_save");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ جاري الحفظ...";
      }
      const res = await window.apiService.saveMaterialLayout(state.materialId, state.elements, "default_text");
      if (btn) {
        btn.disabled = false;
        btn.textContent = "💾 حفظ التنسيق والأحجام نهائياً";
      }
      if (res && !res.error) {
        toast("✅ تم حفظ الإحداثيات والخطوط بنجاح!");
      } else {
        toast("❌ تعذر الحفظ: " + (res?.error || "خطأ بالسيرفر"));
      }
    });

    qs("studio_btn_replace_pdf")?.addEventListener("click", () => {
      if (!state.materialId) return toast("⚠️ اختر المذكرة أولاً");
      qs("studio_pdf_file")?.click();
    });

    qs("studio_pdf_file")?.addEventListener("change", async (e) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file || !state.materialId) return;
      if (!String(file.name || "").toLowerCase().endsWith(".pdf")) {
        return toast("⚠️ يرجى اختيار ملف PDF فقط");
      }

      const btn = qs("studio_btn_replace_pdf");
      if (btn) {
        btn.disabled = true;
        btn.textContent = "⏳ جاري رفع البديل...";
      }
      setLoading(true);
      const res = await window.apiService.replaceMaterialPdf(state.materialId, file);
      setLoading(false);
      if (btn) {
        btn.disabled = false;
        btn.textContent = "📄 استبدال ملف الـ PDF";
      }

      if (res && !res.error) {
        const newPages = Math.max(1, Number(res.total_pages || state.totalPages || 1));
        state.totalPages = newPages;
        state.currentPage = clamp(state.currentPage, 1, newPages);
        const matObj = (state.materials || []).find(m => Number(m.id) === Number(state.materialId));
        if (matObj) matObj.total_pages = newPages;
        const opt = Array.from(qs("studio_mat_select")?.options || []).find(
          o => String(o.value) === String(state.materialId)
        );
        if (opt && matObj) {
          const pubMark = matObj.is_published === false ? " [مخفية]" : "";
          const frmMark = state.pageFramePath ? " 🖼️" : "";
          opt.textContent = `#${matObj.id} — ${matObj.title} (${newPages} ص)${frmMark}${pubMark}`;
        }
        updatePageLabel();
        await renderCanvasImage();
        toast(`✅ تم استبدال ملف الـ PDF (${newPages} ص) مع الحفاظ على جميع الأختام والبيانات!`);
      } else {
        toast("❌ تعذر استبدال الملف: " + (res?.error || "خطأ بالسيرفر"));
      }
    });
  }

  window.initMobileAdminStudio = async function (preferMatId = null) {
    if (!state.initialized) {
      state.initialized = true;
      setupPointerDrag();
      bindControls();
    }
    await loadMaterialOptions(preferMatId);
  };
})();

