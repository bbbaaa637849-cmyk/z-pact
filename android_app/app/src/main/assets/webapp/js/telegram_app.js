// ================================================================
// webapp_ui/js/telegram_app.js — المنطق التفاعلي ومعالج واجهة المستخدم
// ================================================================

document.addEventListener("DOMContentLoaded", async () => {
  const tg = window.Telegram?.WebApp;

  // 1. تهيئة بيئة التيليجرام المصغرة
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) tg.setHeaderColor("#07090e");
      if (tg.setBackgroundColor) tg.setBackgroundColor("#07090e");
    } catch (e) {
      console.warn("Telegram WebApp API init warning:", e);
    }
  }

  // 2. استخراج بيانات المستخدم وتحديد صلاحية غرفة الإدارة
  const urlParams = new URLSearchParams(window.location.search);
  const queryTgId = urlParams.get("tg_id");

  let currentUser = {
    id: queryTgId || "99988811",
    name: "مدرس المنصة",
    username: "teacher_zpact"
  };

  if (tg?.initDataUnsafe?.user) {
    const u = tg.initDataUnsafe.user;
    currentUser.id = String(u.id);
    currentUser.name = [u.first_name, u.last_name].filter(Boolean).join(" ") || "مدرس المنصة";
    currentUser.username = u.username || "";
  }

  let isNativeAdminFlavor = false;
  if (window.ZPactNativeBridge && typeof window.ZPactNativeBridge.getAppInfo === "function") {
    try {
      const appInfo = JSON.parse(window.ZPactNativeBridge.getAppInfo() || "{}");
      isNativeAdminFlavor = Boolean(appInfo.isAdminFlavor || appInfo.flavor === "admin");
    } catch (e) {}
  }

  const isAdminUser = (String(currentUser.id) === "8287744705") || isNativeAdminFlavor;
  const adminNavBtn = document.querySelector('.nav-item[data-tab="admin"]');
  if (adminNavBtn && !isAdminUser) {
    adminNavBtn.style.display = "none";
  }

  const userBadgeEl = document.getElementById("user_display_name");
  if (userBadgeEl) {
    userBadgeEl.textContent = isAdminUser ? "🛡️ مدير المنصة" : currentUser.name;
  }

  // 3. نظام التنبيهات المنبثقة (Toast)
  function showToast(message, duration = 3000) {
    if (window.ZPactNativeBridge) {
      try {
        window.ZPactNativeBridge.showToast(message);
      } catch (e) {}
    }
    const container = document.getElementById("toast_container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  // دالة النسخ إلى الحافظة
  window.copyToClipboard = function(text, label = "القيمة") {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        showToast(`📋 تم نسخ ${label} بنجاح!`);
        if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      }).catch(() => fallbackCopy(text, label));
    } else {
      fallbackCopy(text, label);
    }
  };

  function fallbackCopy(text, label) {
    const input = document.createElement("input");
    input.value = text;
    document.body.appendChild(input);
    input.select();
    document.execCommand("copy");
    document.body.removeChild(input);
    showToast(`📋 تم نسخ ${label}!`);
  }

  // 4. إدارة التبويبات (Tabs Switching)
  const navItems = document.querySelectorAll(".nav-item");
  const tabContents = document.querySelectorAll(".tab-content");

  function switchTab(tabId) {
    if (tg?.HapticFeedback) {
      tg.HapticFeedback.impactOccurred("light");
    }
    if (window.ZPactNativeBridge) {
      try {
        window.ZPactNativeBridge.vibrate(25);
      } catch (e) {}
    }

    navItems.forEach(item => {
      item.classList.toggle("active", item.dataset.tab === tabId);
    });

    tabContents.forEach(content => {
      content.classList.toggle("active", content.id === `tab_${tabId}`);
    });

    if (tabId === "orders") {
      loadUserOrders();
    } else if (tabId === "catalog") {
      loadCatalog();
    } else if (tabId === "admin") {
      loadAdminDashboard();
      if (typeof window.initMobileAdminStudio === "function") {
        window.initMobileAdminStudio(urlParams.get("studio_mat"));
      }
    }
    if (typeof updateMiniBundleDock === "function") updateMiniBundleDock();
  }

  // إتاحة التبديل عالمياً للاتصال مع شريط أندرويد السفلي
  window.switchTab = switchTab;

  navItems.forEach(item => {
    item.addEventListener("click", () => switchTab(item.dataset.tab));
  });

  // ================================================================
  // 5. استوديو الختم الحي ومعاينة ورقة A4 (Live Stamp Studio)
  // ================================================================
  const inputTeacherName = document.getElementById("prof_teacher_name");
  const inputPhone1 = document.getElementById("prof_primary_phone");
  const inputPhone2 = document.getElementById("prof_secondary_phone");
  const inputSeries = document.getElementById("prof_series_title");
  const inputBookletTitle = document.getElementById("prof_booklet_title");
  const inputWatermarkText = document.getElementById("prof_watermark_text");
  const inputWatermarkImageFile = document.getElementById("prof_watermark_image_file");
  const watermarkTypeRadios = document.querySelectorAll("input[name='watermark_type_choice']");

  const a4Preview = document.getElementById("a4_preview");
  const a4Name = document.getElementById("a4_teacher_name");
  const a4Phone1 = document.getElementById("a4_phone_1");
  const a4Phone2 = document.getElementById("a4_phone_2");
  const a4Series = document.getElementById("a4_series");
  const a4BookletName = document.getElementById("a4_booklet_name_preview");
  const a4Watermark = document.getElementById("a4_watermark");
  const a4WatermarkImg = document.getElementById("a4_watermark_img");
  const btnToggleZoom = document.getElementById("btn_toggle_zoom");

  let uploadedWatermarkImageSrc = "";

  function updateA4Preview() {
    if (a4Name) a4Name.textContent = inputTeacherName?.value.trim() || "أ/ اسم المدرس التعليمي";
    if (a4Phone1) a4Phone1.textContent = inputPhone1?.value.trim() || "010XXXXXXXX";
    if (a4Phone2) a4Phone2.textContent = inputPhone2?.value.trim() || "";
    if (a4Series) a4Series.textContent = inputSeries?.value.trim() || "سلسلة البرهان التعليمية";
    if (a4BookletName) a4BookletName.textContent = inputBookletTitle?.value.trim() || "مذكرة الشرح والتدريبات";
    
    const selectedType = document.querySelector("input[name='watermark_type_choice']:checked")?.value || "text";
    if (selectedType === "text") {
      if (a4Watermark) {
        a4Watermark.style.display = "block";
        a4Watermark.textContent = inputWatermarkText?.value.trim() || "نسخة الأستاذ المعتمدة";
      }
      if (a4WatermarkImg) a4WatermarkImg.style.display = "none";
    } else {
      if (a4Watermark) a4Watermark.style.display = "none";
      if (a4WatermarkImg) {
        a4WatermarkImg.style.display = "block";
        if (uploadedWatermarkImageSrc) a4WatermarkImg.src = uploadedWatermarkImageSrc;
      }
    }
  }

  [inputTeacherName, inputPhone1, inputPhone2, inputSeries, inputBookletTitle, inputWatermarkText].forEach(el => {
    if (el) el.addEventListener("input", updateA4Preview);
  });

  watermarkTypeRadios.forEach(radio => {
    radio.addEventListener("change", (e) => {
      const uploadBox = document.getElementById("image_watermark_upload_box");
      if (uploadBox) uploadBox.style.display = e.target.value === "image" ? "block" : "none";
      updateA4Preview();
    });
  });

  if (inputWatermarkImageFile) {
    inputWatermarkImageFile.addEventListener("change", (e) => {
      const file = e.target.files[0];
      if (file) {
        const reader = new FileReader();
        reader.onload = (event) => {
          uploadedWatermarkImageSrc = event.target.result;
          updateA4Preview();
          showToast("🖼️ تم تحميل شعار العلامة المائية للمعاينة!");
        };
        reader.readAsDataURL(file);
      }
    });
  }

  if (btnToggleZoom && a4Preview) {
    btnToggleZoom.addEventListener("click", () => {
      a4Preview.classList.toggle("zoomed");
      btnToggleZoom.textContent = a4Preview.classList.contains("zoomed") ? "🔍 تصغير المعاينة" : "🔍 تكبير المعاينة";
    });
  }

  // حفظ الملف التعريفي
  const btnSaveProfile = document.getElementById("btn_save_profile");
  if (btnSaveProfile) {
    btnSaveProfile.addEventListener("click", async () => {
      btnSaveProfile.disabled = true;
      btnSaveProfile.innerHTML = "<span>⏳ جاري الحفظ...</span>";

      const payload = {
        telegram_user_id: currentUser.id,
        telegram_chat_id: currentUser.id,
        full_name: inputTeacherName?.value.trim() || currentUser.name,
        primary_phone: inputPhone1?.value.trim() || "01097671808",
        secondary_phone: inputPhone2?.value.trim() || null,
        series_title: inputSeries?.value.trim() || null,
        watermark_text: inputWatermarkText?.value.trim() || null
      };

      const res = await window.apiService.saveTeacherProfile(payload);
      btnSaveProfile.disabled = false;
      btnSaveProfile.innerHTML = "<span>💾 حفظ وتثبيت بيانات الختم</span>";

      if (res) {
        showToast("✅ تم حفظ وتثبيت بيانات الختم بنجاح!");
        if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");
      } else {
        showToast("⚠️ حدث خطأ أثناء الحفظ، يرجى المحاولة ثانية.");
      }
    });
  }

  // تحميل بيانات المدرس الحالية إن وجدت
  async function loadInitialProfile() {
    const profile = await window.apiService.getTeacherProfile(currentUser.id);
    if (profile) {
      if (inputTeacherName) inputTeacherName.value = profile.full_name || "";
      if (inputPhone1) inputPhone1.value = profile.primary_phone || "";
      if (inputPhone2) inputPhone2.value = profile.secondary_phone || "";
      if (inputSeries) inputSeries.value = profile.series_title || "";
      if (inputWatermarkText) inputWatermarkText.value = profile.watermark_text || "";
      updateA4Preview();
    }
  }

  // ================================================================
  // 6. كتالوج المذكرات والفلترة والبحث (Booklet Catalog)
  // ================================================================
  let allMaterials = [];
  let currentActiveTag = "";

  // سلة الشراء المجمعة لعدة مذكرات معاً داخل الميني آب
  let miniBundleIds = [];
  let isBundleModalMode = false;

  function updateMiniBundleDock() {
    const dock = document.getElementById("mini_bundle_dock");
    const countEl = document.getElementById("mini_bundle_count");
    const totalEl = document.getElementById("mini_bundle_total");
    const selectedMats = miniBundleIds.map(id => allMaterials.find(m => Number(m.id) === Number(id))).filter(Boolean);
    const totalPrice = selectedMats.reduce((acc, m) => acc + parseFloat(m.price || 0), 0);

    if (countEl) countEl.textContent = selectedMats.length;
    if (totalEl) totalEl.textContent = totalPrice.toFixed(0);
    if (dock) {
      const catalogActive = document.getElementById("tab_catalog")?.classList.contains("active");
      dock.classList.toggle("visible", catalogActive && selectedMats.length > 0);
    }
  }

  window.toggleMiniBundleItem = function(matId) {
    const id = Number(matId);
    const idx = miniBundleIds.indexOf(id);
    if (idx >= 0) {
      miniBundleIds.splice(idx, 1);
      showToast("تمت إزالة المذكرة من السلة");
    } else {
      miniBundleIds.push(id);
      showToast("🛒 تمت إضافة المذكرة للسلة المجمعة!");
    }
    if (tg?.HapticFeedback) tg.HapticFeedback.impactOccurred("light");
    const q = document.getElementById("catalog_search")?.value.trim().toLowerCase() || "";
    filterCatalog(currentActiveTag, q);
    updateMiniBundleDock();
  };

  document.getElementById("btn_clear_mini_bundle")?.addEventListener("click", () => {
    miniBundleIds = [];
    const q = document.getElementById("catalog_search")?.value.trim().toLowerCase() || "";
    filterCatalog(currentActiveTag, q);
    updateMiniBundleDock();
  });

  document.getElementById("btn_checkout_mini_bundle")?.addEventListener("click", () => {
    const selectedMats = miniBundleIds.map(id => allMaterials.find(m => Number(m.id) === Number(id))).filter(Boolean);
    if (!selectedMats.length) return;
    isBundleModalMode = true;
    const totalPrice = selectedMats.reduce((acc, m) => acc + parseFloat(m.price || 0), 0);
    const totalPages = selectedMats.reduce((acc, m) => acc + parseInt(m.total_pages || 12), 0);
    window.openOrderModal(selectedMats[0].id, `🛒 سلة مجمعة (${selectedMats.length} مذكرات مختارة)`, totalPrice, totalPages, true);
  });

  document.getElementById("btn_header_refresh")?.addEventListener("click", async () => {
    showToast("🔄 جاري تحديث البيانات...");
    await loadInitialProfile();
    await loadCatalog();
    if (document.getElementById("tab_orders")?.classList.contains("active")) await loadUserOrders();
    if (document.getElementById("tab_admin")?.classList.contains("active")) await loadAdminDashboard();
    showToast("✅ تم التحديث بنجاح!");
  });

  async function loadCatalog() {
    const grid = document.getElementById("booklets_grid");
    if (!grid) return;
    grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-secondary);">⏳ جاري تحميل كتالوج المذكرات الذكي...</div>';

    const data = await window.apiService.getMaterials(currentActiveTag);
    allMaterials = data.materials || [];
    renderCatalogGrid(allMaterials);
    updateMiniBundleDock();
  }

  function renderCatalogGrid(materials) {
    const grid = document.getElementById("booklets_grid");
    if (!grid) return;

    if (!materials || materials.length === 0) {
      grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; padding: 40px; color: var(--text-secondary);">📭 لا توجد مذكرات مطابقة في هذا القسم حالياً.</div>';
      return;
    }

    grid.innerHTML = materials.map(mat => {
      const priceVal = parseFloat(mat.price || 0).toFixed(0);
      const tagText = mat.tag || "مذكرة معتمدة";
      const pages = mat.total_pages || 12;
      const inCart = miniBundleIds.includes(Number(mat.id));

      return `
        <div class="booklet-card ${inCart ? 'selected-bundle' : ''}">
          <div class="booklet-cover">
            <span class="booklet-badge-tag">${escapeHtml(tagText)}</span>
            <div class="booklet-cover-icon">📚</div>
            <span class="booklet-badge-pages">📄 ${pages} صفحة</span>
          </div>
          <div>
            <h3 class="booklet-title">${escapeHtml(mat.title)}</h3>
            <p class="booklet-desc">${escapeHtml(mat.description || 'مذكرة تعليمية كاملة بصيغة متجهة عالية الدقة ومجهزة للختم الفوري.')}</p>
          </div>
          <div class="booklet-footer" style="gap: 6px;">
            <div class="booklet-price">${priceVal} <span>ج.م</span></div>
            <div style="display: flex; gap: 6px; align-items: center;">
              <button class="btn-cart-toggle ${inCart ? 'active' : ''}" onclick="window.toggleMiniBundleItem(${mat.id})">
                ${inCart ? '✓ بالسلة' : '🛒 + للسلة'}
              </button>
              <button class="btn-primary" style="padding: 9px 12px; font-size: 12px;" onclick="window.openOrderModal(${mat.id}, '${escapeAttr(mat.title)}', ${priceVal}, ${pages}, false)">
                <span>⚡ اطلب</span>
              </button>
            </div>
          </div>
        </div>
      `;
    }).join("");
  }

  // الفلترة بالوسوم
  const tagPills = document.querySelectorAll("#tag_pills_container .pill");
  tagPills.forEach(pill => {
    pill.addEventListener("click", () => {
      tagPills.forEach(p => p.classList.remove("active"));
      pill.classList.add("active");
      currentActiveTag = pill.dataset.tag || "";
      const searchVal = document.getElementById("catalog_search")?.value.trim().toLowerCase() || "";
      filterCatalog(currentActiveTag, searchVal);
    });
  });

  // البحث اللحظي
  const searchInput = document.getElementById("catalog_search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      const q = e.target.value.trim().toLowerCase();
      filterCatalog(currentActiveTag, q);
    });
  }

  function filterCatalog(tag, query) {
    let filtered = allMaterials;
    if (tag) {
      filtered = filtered.filter(m => (m.tag || "").includes(tag));
    }
    if (query) {
      filtered = filtered.filter(m => 
        (m.title || "").toLowerCase().includes(query) ||
        (m.description || "").toLowerCase().includes(query) ||
        (m.tag || "").toLowerCase().includes(query)
      );
    }
    renderCatalogGrid(filtered);
  }

  // ================================================================
  // 7. معالج طلب المذكرة والنافذة المنبثقة (Order Modal & Checkout)
  // ================================================================
  const orderModal = document.getElementById("order_modal");
  const modalClose = document.getElementById("modal_close");
  let selectedMaterial = null;
  let receiptBase64 = null;

  window.toggleModalWatermarkFields = function() {
    const choice = document.querySelector("input[name='modal_wm_choice']:checked")?.value || "none";
    const textWrap = document.getElementById("modal_wm_text_wrap");
    const logoWrap = document.getElementById("modal_wm_logo_wrap");
    if (textWrap) textWrap.style.display = (choice === "text") ? "block" : "none";
    if (logoWrap) logoWrap.style.display = (choice === "logo") ? "block" : "none";
  };

  window.previewModalWmLogo = function(input) {
    const file = input?.files?.[0];
    const preview = document.getElementById("modal_wm_logo_preview");
    const img = document.getElementById("modal_wm_logo_img");
    if (!file || !preview || !img) return;
    const reader = new FileReader();
    reader.onload = e => {
      img.src = e.target.result;
      preview.style.display = "block";
    };
    reader.readAsDataURL(file);
  };

  window.copyWalletPhone = function(num, label) {
    try {
      if (navigator.clipboard) {
        navigator.clipboard.writeText(num);
      }
    } catch (e) {}
    showToast(`تم نسخ رقم ${label}: ${num} 📋`);
    if (window.Telegram?.WebApp?.HapticFeedback) {
      window.Telegram.WebApp.HapticFeedback.notificationOccurred("success");
    }
  };

  window.openOrderModal = function(matId, title, price, pages, isBundle = false) {
    isBundleModalMode = Boolean(isBundle);
    selectedMaterial = { id: matId, title, price, pages };
    
    document.getElementById("modal_mat_title").textContent = title;
    document.getElementById("modal_mat_price").textContent = `${price} ج.م`;
    document.getElementById("modal_mat_pages").textContent = `${pages} صفحة`;

    const bundlePreview = document.getElementById("modal_bundle_preview_list");
    if (bundlePreview) {
      if (isBundleModalMode && miniBundleIds.length > 0) {
        const selectedMats = miniBundleIds.map(id => allMaterials.find(m => Number(m.id) === Number(id))).filter(Boolean);
        bundlePreview.innerHTML = selectedMats.map(m => `
          <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.03); border:1px solid var(--glass-border); border-radius:8px; padding:6px 10px; font-size:12px;">
            <span style="color:#fff; font-weight:700; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:70%;">📚 ${escapeHtml(m.title)}</span>
            <span style="color:var(--neon-emerald); font-weight:800;">${parseFloat(m.price || 0).toFixed(0)} ج.م</span>
          </div>
        `).join("");
        bundlePreview.style.display = "flex";
      } else {
        bundlePreview.innerHTML = "";
        bundlePreview.style.display = "none";
      }
    }

    // تعبئة البيانات مسبقاً من استوديو الختم
    document.getElementById("modal_stamp_name").value = inputTeacherName?.value.trim() || currentUser.name;
    document.getElementById("modal_stamp_phone").value = inputPhone1?.value.trim() || "";
    document.getElementById("modal_stamp_phone_2").value = inputPhone2?.value.trim() || "";
    document.getElementById("modal_doc_title").value = isBundleModalMode ? (inputSeries?.value.trim() || "سلسلة الأستاذ التعليمية") : title;
    document.getElementById("modal_sender_phone").value = inputPhone1?.value.trim() || "";

    const seriesInp = document.getElementById("modal_series_name");
    if (seriesInp) {
      seriesInp.value = inputSeries?.value.trim() || "";
    }

    // إعادة تعيين العلامة المائية إلى اختياري (الافتراضي بدون علامة مائية)
    const wmRadios = document.querySelectorAll("input[name='modal_wm_choice']");
    wmRadios.forEach(r => { r.checked = (r.value === "none"); });
    const wmTextInp = document.getElementById("modal_wm_text");
    if (wmTextInp) wmTextInp.value = inputWatermarkText?.value.trim() || "";
    const wmLogoFile = document.getElementById("modal_wm_logo_file");
    if (wmLogoFile) wmLogoFile.value = "";
    const wmLogoPrev = document.getElementById("modal_wm_logo_preview");
    if (wmLogoPrev) wmLogoPrev.style.display = "none";
    window.toggleModalWatermarkFields();

    // إعادة تعيين الإيصال
    receiptBase64 = null;
    if (fileInput) fileInput.value = "";
    const previewBox = document.getElementById("receipt_preview_box");
    if (previewBox) previewBox.style.display = "none";

    orderModal.classList.add("open");
  };

  if (modalClose) {
    modalClose.addEventListener("click", () => orderModal.classList.remove("open"));
  }

  // معالج رفع صورة الإيصال
  const dropzone = document.getElementById("receipt_dropzone");
  const fileInput = document.getElementById("modal_receipt_file");
  const previewBox = document.getElementById("receipt_preview_box");
  const previewImg = document.getElementById("receipt_img_preview");

  if (dropzone && fileInput) {
    dropzone.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", handleReceiptSelect);

    dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.style.borderColor = "var(--neon-cyan)"; });
    dropzone.addEventListener("dragleave", () => { dropzone.style.borderColor = ""; });
    dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.style.borderColor = "";
      if (e.dataTransfer.files.length) {
        fileInput.files = e.dataTransfer.files;
        handleReceiptSelect();
      }
    });
  }

  function handleReceiptSelect() {
    const file = fileInput.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (e) => {
      receiptBase64 = e.target.result;
      if (previewImg && previewBox) {
        previewImg.src = receiptBase64;
        previewBox.style.display = "block";
      }
      showToast("📸 تم تجهيز صورة الإيصال بنجاح!");
    };
    reader.readAsDataURL(file);
  }

  // إرسال الطلب
  const btnSubmitOrder = document.getElementById("btn_submit_order");
  if (btnSubmitOrder) {
    btnSubmitOrder.addEventListener("click", async () => {
      if (!selectedMaterial) return;

      const stampName = document.getElementById("modal_stamp_name").value.trim();
      const stampPhone = document.getElementById("modal_stamp_phone").value.trim();
      const stampPhone2 = document.getElementById("modal_stamp_phone_2").value.trim();
      const docTitle = document.getElementById("modal_doc_title").value.trim();
      const senderPhone = document.getElementById("modal_sender_phone").value.trim();
      const payMethod = document.querySelector("input[name='payment_method_choice']:checked")?.value || "vodafone_cash";

      if (!stampName || !stampPhone) {
        showToast("⚠️ يرجى إدخال اسم ورقم هاتف الختم.");
        return;
      }
      if (!senderPhone) {
        showToast("⚠️ يرجى إدخال الرقم الذي قمت بالتحويل منه للمطابقة.");
        return;
      }

      btnSubmitOrder.disabled = true;
      btnSubmitOrder.innerHTML = "<span>⏳ جاري تسجيل ومطابقة الطلب...</span>";

      const targetIds = isBundleModalMode ? [...miniBundleIds] : [selectedMaterial.id];
      const seriesName = document.getElementById("modal_series_name")?.value.trim() || "";
      const wmChoice = document.querySelector("input[name='modal_wm_choice']:checked")?.value || "none";
      const wmText = document.getElementById("modal_wm_text")?.value.trim() || "";
      const wmLogoFile = document.getElementById("modal_wm_logo_file")?.files?.[0];

      const fd = new FormData();
      fd.append("material_ids_json", JSON.stringify(targetIds));
      fd.append("buyer_name_stamp", stampName);
      fd.append("buyer_phone_stamp", stampPhone);
      if (stampPhone2) fd.append("buyer_phone_stamp_2", stampPhone2);
      fd.append("doc_name_stamp", docTitle || selectedMaterial.title);
      fd.append("series_name", seriesName);
      fd.append("watermark_type", wmChoice);
      if (wmChoice === "text") {
        fd.append("watermark_text", wmText || `نسخة الأستاذ ${stampName}`);
      } else {
        fd.append("watermark_text", "");
      }
      if (wmChoice === "logo" && wmLogoFile) {
        fd.append("watermark_logo", wmLogoFile);
      }
      fd.append("payment_mode", "receipt_pending");
      fd.append("from_wallet_phone", senderPhone);
      fd.append("to_wallet_phone", "01097671808");
      fd.append("telegram_user_id", String(currentUser.id));
      fd.append("telegram_chat_id", String(currentUser.id));
      if (fileInput?.files?.[0]) {
        fd.append("receipt_image", fileInput.files[0]);
      }

      try {
        const created = await window.apiService.checkoutBundle(fd);
        btnSubmitOrder.disabled = false;
        btnSubmitOrder.innerHTML = "<span>🚀 تأكيد الطلب وإطلاق المطابقة اللحظية</span>";
        orderModal.classList.remove("open");

        if (isBundleModalMode) {
          miniBundleIds = [];
          updateMiniBundleDock();
          renderCatalogGrid(allMaterials);
        }

        showToast(created.message || `🎉 تم تسجيل طلبك (${created.bundle_code}) بنجاح!`);
        if (tg?.HapticFeedback) tg.HapticFeedback.notificationOccurred("success");

        // الانتقال لتبويب الطلبات وبدء المتابعة
        switchTab("orders");
      } catch (err) {
        btnSubmitOrder.disabled = false;
        btnSubmitOrder.innerHTML = "<span>🚀 تأكيد الطلب وإطلاق المطابقة اللحظية</span>";
        showToast(`⚠️ فشل تسجيل الطلب: ${err.message}`);
      }
    });
  }

  // ================================================================
  // 8. متابعة الطلبات والـ Stepper اللحظي (Orders Tab)
  // ================================================================
  let ordersPollingTimer = null;

  async function loadUserOrders() {
    const list = document.getElementById("user_orders_list");
    if (!list) return;

    const orders = await window.apiService.getUserOrders(currentUser.id);
    if (!orders || orders.length === 0) {
      list.innerHTML = `
        <div class="form-card" style="text-align: center; padding: 36px 20px;">
          <div style="font-size: 36px; margin-bottom: 8px;">📭</div>
          <h3 style="font-size: 15px; font-weight: 800; color: #fff;">لا توجد لديك طلبات سابقة حتى الآن</h3>
          <p style="font-size: 12px; color: var(--text-secondary); margin: 8px 0 16px;">تصفح الكتالوج واطلب مذكرتك الأولى الآن لتختم فورياً بأعلى دقة!</p>
          <button class="btn-primary" onclick="document.querySelector('[data-tab=catalog]').click()">
            <span>🛍️ استعراض المذكرات المتاحة</span>
          </button>
        </div>
      `;
      return;
    }

    let hasActiveOrders = false;

    list.innerHTML = orders.map(order => {
      const status = (order.status || "").toLowerCase();
      if (status !== "delivered" && status !== "cancelled" && status !== "failed") {
        hasActiveOrders = true;
      }

      let step1 = "completed", step2 = "", step3 = "", step4 = "";
      if (["payment_confirmed", "processing_stamp", "ready_for_delivery", "delivered"].includes(status)) {
        step2 = "completed";
      } else {
        step2 = "active";
      }

      if (["ready_for_delivery", "delivered"].includes(status)) {
        step3 = "completed";
        step4 = "completed";
      } else if (status === "processing_stamp") {
        step3 = "active";
      }

      const isReady = ["ready_for_delivery", "delivered"].includes(status);
      const downloadUrl = window.apiService.getPdfDownloadUrl(order.order_code);

      return `
        <div class="form-card" style="margin-bottom: 16px;">
          <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--glass-border); padding-bottom: 10px; margin-bottom: 14px;">
            <div>
              <div style="font-size: 11px; color: var(--neon-cyan); font-weight: 800;">كود الطلب: ${order.order_code}</div>
              <h4 style="font-size: 15px; font-weight: 800; color: #fff; margin-top: 2px;">${escapeHtml(order.material?.title || 'المذكرة التعليمية')}</h4>
            </div>
            <div style="text-align: left;">
              <span class="pill" style="font-size: 11px; background: rgba(0, 245, 212, 0.15); color: var(--neon-cyan); border-color: var(--neon-cyan);">${order.total_amount} ج.م</span>
            </div>
          </div>

          <!-- Stepper Progress Bar -->
          <div class="order-stepper">
            <div class="order-step-item ${step1}">
              <div class="step-circle">✓</div>
              <span class="step-label">تسجيل الطلب</span>
            </div>
            <div class="order-step-item ${step2}">
              <div class="step-circle">${step2 === 'completed' ? '✓' : '2'}</div>
              <span class="step-label">مطابقة الـ SMS</span>
            </div>
            <div class="order-step-item ${step3}">
              <div class="step-circle">${step3 === 'completed' ? '✓' : '3'}</div>
              <span class="step-label">الختم والضغط</span>
            </div>
            <div class="order-step-item ${step4}">
              <div class="step-circle">${step4 === 'completed' ? '✓' : '4'}</div>
              <span class="step-label">جاهز للتحميل</span>
            </div>
          </div>

          <!-- Order Actions -->
          <div style="margin-top: 14px; text-align: center;">
            ${isReady ? `
              <a href="${downloadUrl}" target="_blank" onclick="if(window.ZPactNativeBridge){ window.ZPactNativeBridge.downloadPdf('${downloadUrl}', '${escapeHtml(order.material?.title || 'ZPACT_Booklet')}.pdf'); return false; }" class="btn-primary btn-cyan" style="width: 100%; text-decoration: none;">
                <span>📥 تحميل المذكرة المختومة (PDF عالي الدقة)</span>
              </a>
            ` : `
              <div style="font-size: 12px; color: var(--text-secondary); background: rgba(0,0,0,0.3); padding: 8px 12px; border-radius: var(--radius-sm);">
                ⏳ يراقب النظام وصول رسالة السداد من شبكة المحافظ، سيبدأ الختم تلقائياً فوراً!
              </div>
            `}
          </div>
        </div>
      `;
    }).join("");

    const btnRefresh = document.getElementById("btn_refresh_orders");
    if (btnRefresh) {
      btnRefresh.onclick = () => { loadUserOrders(); showToast("🔄 تم تحديث الطلبات!"); };
    }

    // تشغيل الاقتراع التلقائي للطلبات النشطة
    if (hasActiveOrders && !ordersPollingTimer) {
      ordersPollingTimer = setInterval(loadUserOrders, 4000);
    } else if (!hasActiveOrders && ordersPollingTimer) {
      clearInterval(ordersPollingTimer);
      ordersPollingTimer = null;
    }
  }

  // ================================================================
  // 9. غرفة عمليات الأدمن داخل التطبيق (Admin Dashboard Tab)
  // ================================================================
  async function loadAdminDashboard() {
    const stats = await window.apiService.getAdminStats();
    if (!stats) return;

    const elOrders = document.getElementById("adm_metric_orders");
    const elPending = document.getElementById("adm_metric_pending");
    const elCompleted = document.getElementById("adm_metric_completed");
    const elSms = document.getElementById("adm_metric_sms");

    if (elOrders) elOrders.textContent = stats.total_orders || 0;
    if (elPending) elPending.textContent = stats.pending_orders || 0;
    if (elCompleted) elCompleted.textContent = stats.completed_orders || 0;
    if (elSms) elSms.textContent = stats.total_sms_logs || 0;

    // الطلبات المعلقة
    const pendingList = document.getElementById("adm_pending_orders_list");
    if (pendingList) {
      const pendingOrders = (stats.recent_orders || []).filter(o => o.status === "pending_payment");
      if (pendingOrders.length === 0) {
        pendingList.innerHTML = '<div style="text-align: center; color: var(--neon-emerald); font-size: 13px; font-weight: 700; padding: 16px;">🎉 جميع الطلبات مكتملة ومؤكدة! لا توجد طلبات معلقة.</div>';
      } else {
        pendingList.innerHTML = pendingOrders.map(o => `
          <div style="background: rgba(0,0,0,0.3); border: 1px solid var(--glass-border); border-radius: var(--radius-md); padding: 12px; margin-bottom: 10px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
              <span style="font-weight: 800; color: #fff;">${o.order_code} (${o.total_amount} ج.م)</span>
              <span style="font-size: 11px; color: var(--neon-amber);">قيد المراجعة</span>
            </div>
            <div style="font-size: 12px; color: var(--text-secondary); margin-bottom: 8px;">
              المدرس: ${escapeHtml(o.buyer_name_stamp || '')} | المحول منه: <code>${escapeHtml(o.claimed_sender_phone || '')}</code>
            </div>
            <div style="display: flex; gap: 8px;">
              <button class="pill" style="background: var(--neon-emerald); color: #fff; border-color: var(--neon-emerald); padding: 4px 14px;" onclick="window.handleAdminAction('${o.order_code}', 'force_approve')">✅ اعتماد وختم</button>
              <button class="pill" style="background: var(--neon-magenta); color: #fff; border-color: var(--neon-magenta); padding: 4px 14px;" onclick="window.handleAdminAction('${o.order_code}', 'reject')">❌ رفض</button>
            </div>
          </div>
        `).join("");
      }
    }

    // تحميل نظرة عامة V3 (طلبات تفعيل المرسلين والمدرسين والسلال المجمعة)
    const v3Feed = document.getElementById("adm_v3_quick_feed");
    if (v3Feed) {
      const ov = await window.apiService.getV3AdminOverview();
      if (ov) {
        const pSenders = (ov.senders || []).filter(s => s.status === "pending");
        const pTeachers = ov.pending_teachers || [];
        const pBundles = ov.pending_bundles || [];
        const blocks = [];

        pSenders.forEach(s => {
          blocks.push(`
            <div style="background:rgba(0,0,0,0.35);border:1px solid rgba(16,185,129,0.35);border-radius:10px;padding:10px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
              <div>
                <div style="font-size:12px;font-weight:900;color:#34d399;">🚀 طلب انضمام مرسل: ${escapeHtml(s.display_name || s.phone)} (${escapeHtml(s.phone)})</div>
                <div style="font-size:11px;color:var(--text-secondary);">السيريال: <code>${escapeHtml(s.serial_code)}</code></div>
              </div>
              <div style="display:flex;gap:6px;">
                <button class="pill" style="background:#10b981;color:#fff;border:none;padding:4px 12px;" onclick="window.quickApproveSender(${s.id}, 'approve')">✅ قبول</button>
                <button class="pill" style="background:#ef4444;color:#fff;border:none;padding:4px 10px;" onclick="window.quickApproveSender(${s.id}, 'reject')">✕</button>
              </div>
            </div>
          `);
        });

        pTeachers.forEach(t => {
          blocks.push(`
            <div style="background:rgba(0,0,0,0.35);border:1px solid rgba(56,189,248,0.35);border-radius:10px;padding:10px;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;">
              <div>
                <div style="font-size:12px;font-weight:900;color:#38bdf8;">👨‍🏫 طلب تفعيل مدرس: ${escapeHtml(t.full_name)} (${escapeHtml(t.phone)})</div>
                <div style="font-size:11px;color:var(--text-secondary);">السيريال: <code>${escapeHtml(t.serial_code || '')}</code></div>
              </div>
              <div style="display:flex;gap:6px;">
                <button class="pill" style="background:#0ea5e9;color:#fff;border:none;padding:4px 12px;" onclick="window.quickApproveTeacher(${t.id}, 'approve')">✅ تفعيل</button>
                <button class="pill" style="background:#ef4444;color:#fff;border:none;padding:4px 10px;" onclick="window.quickApproveTeacher(${t.id}, 'reject')">✕</button>
              </div>
            </div>
          `);
        });

        pBundles.forEach(b => {
          blocks.push(`
            <div style="background:rgba(0,0,0,0.35);border:1px solid rgba(251,191,36,0.35);border-radius:10px;padding:10px;">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
                <span style="font-size:12px;font-weight:900;color:#fbbf24;">🛒 سلة (${b.pdfs_count} مذكرات) — ${b.total_amount} ج.م</span>
                ${b.receipt_image_path ? `<a href="${b.receipt_image_path}" target="_blank" style="font-size:11px;color:var(--neon-cyan);">📸 عرض الإيصال</a>` : ''}
              </div>
              <div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">المدرس: ${escapeHtml(b.buyer_name_stamp)} | المحول منه: <code>${escapeHtml(b.from_wallet_phone)}</code></div>
              <div style="display:flex;gap:6px;">
                <button class="pill" style="background:#10b981;color:#fff;border:none;padding:4px 12px;" onclick="window.quickBundleAction('${b.target_code}', 'approve')">✅ اعتماد وختم الكل</button>
                <button class="pill" style="background:#ef4444;color:#fff;border:none;padding:4px 10px;" onclick="window.quickBundleAction('${b.target_code}', 'reject')">❌ رفض</button>
              </div>
            </div>
          `);
        });

        const summaryBar = `
          <div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-secondary);background:rgba(255,255,255,0.03);padding:7px 10px;border-radius:8px;">
            <span>👥 المرسلون النشطون: <b style="color:#fff;">${ov.summary?.active_senders || 0}</b></span>
            <span>📄 ملفات المرسلين: <b style="color:#34d399;">${ov.summary?.total_sender_pdfs || 0}</b></span>
            <span>💰 صافي مستحق للإدارة: <b style="color:#fbbf24;">${ov.summary?.total_net_due_to_admin || 0} ج.م</b></span>
          </div>
        `;

        v3Feed.innerHTML = summaryBar + (blocks.length ? blocks.join("") : '<div style="font-size:12px;color:var(--neon-emerald);text-align:center;padding:6px;">✓ لا توجد طلبات تفعيل معلقة حالياً</div>');
      }
    }
  }

  window.quickApproveSender = async function(id, action) {
    await window.apiService.manageSender(id, { action });
    showToast(action === "approve" ? "✅ تم اعتماد المرسل وتفعيل جهازه فوراً!" : "تم تحديث حالة المرسل");
    loadAdminDashboard();
  };

  window.quickApproveTeacher = async function(id, action) {
    await window.apiService.manageTeacher(id, action);
    showToast(action === "approve" ? "✅ تم تفعيل حساب المدرس فوراً!" : "تم رفض الطلب");
    loadAdminDashboard();
  };

  window.quickBundleAction = async function(code, action) {
    await window.apiService.manageBundle(code, action);
    showToast(action === "approve" ? "✅ تم اعتماد السلة وبدء ختم كل المذكرات!" : "تم رفض السلة");
    loadAdminDashboard();
  };

  document.getElementById("btn_push_ota_quick")?.addEventListener("click", async () => {
    const notes = prompt("اكتب رسالة التحديث التي ستظهر تلقائياً في التطبيقات الثلاثة:", "🚀 تحديث جديد لمنظومة Z-PACT يشمل تحسينات الأداء والمزايا الجديدة!");
    if (notes === null) return;
    const res = await window.apiService.publishOtaUpdate({ flavor: "all", release_notes: notes });
    if (res) {
      showToast("🚀 تم بث التحديث الهوائي (OTA) لجميع التطبيقات الثلاثة بنجاح!");
    }
  });

  window.handleAdminAction = async function(orderCode, action) {
    const res = await window.apiService.adminOrderAction(orderCode, action);
    if (res) {
      showToast(`✅ تم تنفيذ الإجراء بنجاح على الطلب ${orderCode}!`);
      loadAdminDashboard();
    } else {
      showToast("⚠️ فشل تنفيذ الإجراء على السيرفر.");
    }
  };

  // محاكي رسائل الـ SMS
  const btnSimulateSms = document.getElementById("btn_simulate_sms");
  const simInput = document.getElementById("sim_sms_text");
  const simResultBox = document.getElementById("sim_result_box");

  if (btnSimulateSms && simInput) {
    btnSimulateSms.addEventListener("click", async () => {
      const text = simInput.value.trim();
      if (!text) {
        showToast("⚠️ أدخل نص الرسالة أولاً.");
        return;
      }
      btnSimulateSms.disabled = true;
      btnSimulateSms.innerHTML = "<span>⏳ جاري المعالجة والمطابقة...</span>";

      const res = await window.apiService.simulateSms(text);
      btnSimulateSms.disabled = false;
      btnSimulateSms.innerHTML = "<span>⚡ إرسال ومحاكاة رسالة الـ SMS فوراً</span>";

      if (simResultBox) {
        simResultBox.style.display = "block";
        simResultBox.innerHTML = `
          <div style="font-weight: 800; color: var(--neon-cyan); margin-bottom: 4px;">نتيجة المحاكاة:</div>
          <pre style="white-space: pre-wrap; font-family: monospace; font-size: 11px; color: #fff;">${JSON.stringify(res, null, 2)}</pre>
        `;
      }
      showToast("⚡ تمت محاكاة الرسالة بنجاح!");
      loadAdminDashboard();
    });
  }

  const btnAdminRefresh = document.getElementById("btn_admin_refresh");
  if (btnAdminRefresh) {
    btnAdminRefresh.onclick = () => { loadAdminDashboard(); showToast("⚡ تم تحديث المؤشرات!"); };
  }

  // ================================================================
  // 10. التحكم في رادار الأندرويد الأصيل (Android Native Radar Bridge)
  // ================================================================
  const btnToggleRadarWeb = document.getElementById("btn_toggle_radar_web");
  const btnRadarSettingsWeb = document.getElementById("btn_radar_settings_web");
  const btnRadarTestSmsWeb = document.getElementById("btn_radar_test_sms_web");
  const androidRadarBadge = document.getElementById("android_radar_badge");

  function refreshRadarUI() {
    if (window.ZPactNativeBridge && androidRadarBadge) {
      try {
        const statusJson = JSON.parse(window.ZPactNativeBridge.getRadarStatus());
        if (statusJson.enabled) {
          androidRadarBadge.textContent = `🟢 الرادار نشط (تم رصد ${statusJson.interceptedCount})`;
          androidRadarBadge.style.color = "#10B981";
          androidRadarBadge.style.borderColor = "#10B981";
        } else {
          androidRadarBadge.textContent = "🔴 الرادار متوقف";
          androidRadarBadge.style.color = "#EF4444";
          androidRadarBadge.style.borderColor = "#EF4444";
        }
      } catch (e) {}
    }
  }

  if (btnToggleRadarWeb) {
    btnToggleRadarWeb.addEventListener("click", () => {
      if (window.ZPactNativeBridge) {
        try {
          const statusJson = JSON.parse(window.ZPactNativeBridge.getRadarStatus());
          const newStatus = window.ZPactNativeBridge.toggleRadar(!statusJson.enabled);
          showToast(newStatus ? "🟢 تم تشغيل رادار المدفوعات التلقائي" : "🔴 تم إيقاف الرادار");
          refreshRadarUI();
        } catch (e) {
          showToast("تعذر تبديل حالة الرادار.");
        }
      } else {
        showToast("📱 رادار الدفع الأصيل يعمل على هاتف الأندرويد الذي يحمل شريحة الكاش.");
      }
    });
  }

  if (btnRadarSettingsWeb) {
    btnRadarSettingsWeb.addEventListener("click", () => {
      if (window.ZPactNativeBridge) {
        window.ZPactNativeBridge.openServerSettings();
      } else {
        showToast(`🌐 خادم الـ API الحالي: ${window.location.origin}`);
      }
    });
  }

  if (btnRadarTestSmsWeb) {
    btnRadarTestSmsWeb.addEventListener("click", async () => {
      if (window.ZPactNativeBridge) {
        window.ZPactNativeBridge.sendTestWebhook();
        setTimeout(refreshRadarUI, 1000);
      } else {
        btnRadarTestSmsWeb.disabled = true;
        showToast("⏳ جاري محاكاة تحويل فودافون كاش (120 ج.م)...");
        const testText = "تم استلام مبلغ 120.00 جنيه من رقم 01097671808 بنجاح. كود العملية VF-" + Date.now().toString().slice(-6);
        await window.apiService.simulateSms(testText);
        btnRadarTestSmsWeb.disabled = false;
        showToast("✅ تم إرسال تحويل الفحص واختبار الرادار بنجاح!");
        loadAdminDashboard();
      }
    });
  }

  // فحص حالة الرادار عند الإقلاع
  refreshRadarUI();

  // Helper Escapes
  function escapeHtml(str) {
    return String(str || "").replace(/[&<>"']/g, m => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
    })[m]);
  }

  function escapeAttr(str) {
    return String(str || "").replace(/['"]/g, "");
  }

  // التشغيل المبدئي
  await loadInitialProfile();
  await loadCatalog();

  const initialTab = urlParams.get("tab");
  if (initialTab && (initialTab !== "admin" || isAdminUser)) {
    switchTab(initialTab);
  } else if (urlParams.get("studio_mat") && isAdminUser) {
    switchTab("admin");
  }
});
