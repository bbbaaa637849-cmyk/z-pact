// ================================================================
// webapp_ui/js/api.js — محرك الاتصال والتكامل مع الـ REST API
// ================================================================

class ApiService {
  constructor() {
    this.baseUrl = window.location.origin;
    this.serviceKey = "zpact_internal_bridge_secret_key_2026";
  }

  getHeaders() {
    return {
      "Content-Type": "application/json",
      "X-Internal-Service-Key": this.serviceKey
    };
  }

  async getMaterials(tag = "", search = "") {
    try {
      const params = new URLSearchParams();
      if (tag && tag !== "all") params.append("tag", tag);
      if (search) params.append("search", search);

      const res = await fetch(`${this.baseUrl}/api/internal/materials?${params.toString()}`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch materials:", err);
      return { total: 0, materials: [] };
    }
  }

  async getTeacherProfile(telegramUserId) {
    if (!telegramUserId) return null;
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/teachers/profile?telegram_user_id=${telegramUserId}`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch teacher profile:", err);
      return null;
    }
  }

  async saveTeacherProfile(profileData) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/teachers/profile`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify(profileData)
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to save teacher profile:", err);
      return null;
    }
  }

  async createOrder(orderPayload) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/orders/create`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify(orderPayload)
      });
      if (!res.ok) {
        const errorData = await res.json().catch(() => ({}));
        throw new Error(errorData.detail || `HTTP error ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to create order:", err);
      throw err;
    }
  }

  async getUserOrders(chatId) {
    if (!chatId) return [];
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/orders/user/${chatId}`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) return [];
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch user orders:", err);
      return [];
    }
  }

  async getOrderStatus(orderCode) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/orders/${orderCode}/status`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch order status:", err);
      return null;
    }
  }

  getPdfDownloadUrl(orderCode) {
    return `${this.baseUrl}/api/internal/orders/${orderCode}/download`;
  }

  // --- لوحة تحكم الأدمن ومحاكاة الـ SMS ---
  async getAdminStats() {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/stats`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch admin stats:", err);
      return null;
    }
  }

  async adminOrderAction(orderCode, action, adminNotes = "") {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/orders/${orderCode}/admin-action`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify({ action, admin_notes: adminNotes })
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to perform admin order action:", err);
      return null;
    }
  }

  // --- V3 Multi-Book Bundles, Senders, Approvals & OTA ---
  async checkoutBundle(formData) {
    const res = await fetch(`${this.baseUrl}/api/v3/bundles/checkout`, {
      method: "POST",
      headers: { "X-Internal-Service-Key": this.serviceKey },
      body: formData
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || `HTTP error ${res.status}`);
    }
    return data;
  }

  async getV3AdminOverview() {
    try {
      const res = await fetch(`${this.baseUrl}/api/v3/admin/overview`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) return null;
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch v3 admin overview:", err);
      return null;
    }
  }

  async manageSender(senderId, payload) {
    const res = await fetch(`${this.baseUrl}/api/v3/admin/senders/${senderId}/action`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload)
    });
    return res.ok ? await res.json() : null;
  }

  async manageTeacher(userId, action) {
    const res = await fetch(`${this.baseUrl}/api/v3/admin/teachers/${userId}/action`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ action })
    });
    return res.ok ? await res.json() : null;
  }

  async manageBundle(targetCode, action, reason = "") {
    const res = await fetch(`${this.baseUrl}/api/v3/admin/bundles/${targetCode}/action`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify({ action, reason })
    });
    return res.ok ? await res.json() : null;
  }

  async publishOtaUpdate(payload) {
    const res = await fetch(`${this.baseUrl}/api/v3/admin/app-updates/publish`, {
      method: "POST",
      headers: this.getHeaders(),
      body: JSON.stringify(payload)
    });
    return res.ok ? await res.json() : null;
  }

  async simulateSms(messageText) {
    try {
      // تجربة المسار الموحد على البورت 8000 ثم fallback إلى 8001
      let url = `${this.baseUrl}/webhook/sms`;
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Webhook-Secret": "zpact_sms_webhook_secret_2026"
        },
        body: JSON.stringify({ message: messageText })
      });
      return await res.json();
    } catch (err) {
      console.warn("Retrying SMS simulation via Port 8001:", err);
      const res2 = await fetch("http://127.0.0.1:8001/webhook/sms", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Webhook-Secret": "zpact_sms_webhook_secret_2026"
        },
        body: JSON.stringify({ message: messageText })
      });
      return await res2.json();
    }
  }
}

Object.assign(ApiService.prototype, {
  async getAdminMaterials(includeUnpublished = true) {
    try {
      const qs = includeUnpublished ? "?include_unpublished=true" : "";
      const res = await fetch(`${this.baseUrl}/api/internal/materials${qs}`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch admin materials:", err);
      return { total: 0, materials: [] };
    }
  },

  async getFonts() {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/fonts`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to fetch fonts:", err);
      return { fonts: [] };
    }
  },

  async getMaterialLayout(materialId) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/layout`, {
        method: "GET",
        headers: this.getHeaders()
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to load material layout:", err);
      return null;
    }
  },

  async saveMaterialLayout(materialId, stampElements, layoutType = "default_text") {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/layout`, {
        method: "PUT",
        headers: this.getHeaders(),
        body: JSON.stringify({ layout_type: layoutType, stamp_elements: stampElements })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP error ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to save material layout:", err);
      return { error: err.message };
    }
  },

  async fetchPreviewPageBlobUrl(materialId, page = 1, stamped = 0, zoom = 1.35) {
    try {
      const params = new URLSearchParams({
        page: String(page),
        stamped: String(stamped ? 1 : 0),
        zoom: String(zoom)
      });
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/preview-page?${params.toString()}`, {
        method: "GET",
        headers: { "X-Internal-Service-Key": this.serviceKey }
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    } catch (err) {
      console.error("❌ Failed to fetch preview page:", err);
      return null;
    }
  },

  async renderLivePreviewBlobUrl(materialId, payload) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/preview-page`, {
        method: "POST",
        headers: this.getHeaders(),
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const blob = await res.blob();
      return URL.createObjectURL(blob);
    } catch (err) {
      console.error("❌ Failed to render live preview:", err);
      return null;
    }
  },

  async uploadMaterialFrame(materialId, file) {
    try {
      const formData = new FormData();
      formData.append("frame_image", file);
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/frame`, {
        method: "POST",
        headers: { "X-Internal-Service-Key": this.serviceKey },
        body: formData
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP error ${res.status}`);
      }
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to upload material frame:", err);
      return { error: err.message };
    }
  },

  async deleteMaterialFrame(materialId) {
    try {
      const res = await fetch(`${this.baseUrl}/api/internal/admin/materials/${materialId}/frame`, {
        method: "DELETE",
        headers: { "X-Internal-Service-Key": this.serviceKey }
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      return await res.json();
    } catch (err) {
      console.error("❌ Failed to delete material frame:", err);
      return { error: err.message };
    }
  }
});

window.apiService = new ApiService();
