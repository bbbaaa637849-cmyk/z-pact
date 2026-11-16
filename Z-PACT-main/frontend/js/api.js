// ================================================================
// frontend/js/api.js — مركز التواصل مع الـ Backend API
//
// كل التواصل مع الـ API بيمر من هنا.
// m مفيش endpoint بيتكلم مع الـ server مباشرة من غير ما يعدي هنا.
// ================================================================

// اكتشاف مسار الـ API تلقائياً بناءً على البيئة (سواء بورت 8000 أو 8088 أو نفق HTTPS)
const isLocal = window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
const isLiveServer = isLocal && window.location.port === "5500";
const BASE_URL = isLiveServer
  ? `${window.location.protocol}//${window.location.hostname}:8000/api`
  : `${window.location.origin}/api`;

window.BASE_URL = BASE_URL;

// ---------------------------------------------------------------
// بصمة وسيريال الجهاز الحقيقي (Hardware Serial + Device Fingerprint)
// ---------------------------------------------------------------
function getDeviceID() {
    try {
        if (window.ZPactNativeBridge && typeof window.ZPactNativeBridge.getDeviceSerial === "function") {
            const hwSerial = window.ZPactNativeBridge.getDeviceSerial();
            if (hwSerial && hwSerial.length > 4) {
                localStorage.setItem("device_id", hwSerial);
                return hwSerial;
            }
        }
    } catch (_) {}
    let id = localStorage.getItem("device_id");
    if (!id) {
        id = 'ZP-WEB-' + Math.random().toString(36).substring(2, 8).toUpperCase() + '-' + Math.random().toString(36).substring(2, 6).toUpperCase();
        localStorage.setItem("device_id", id);
    }
    return id;
}
window.getDeviceID = getDeviceID;

function getAdminAppProof() {
    try {
        if (window.ZPactNativeBridge && typeof window.ZPactNativeBridge.getAdminAppProof === "function") {
            const proof = window.ZPactNativeBridge.getAdminAppProof();
            if (proof) return proof;
        }
    } catch (_) {}
    return localStorage.getItem("zpact_admin_app_proof") || "";
}
window.getAdminAppProof = getAdminAppProof;

// منع السحب للتحديث المزعج في كل صفحات التطبيق والموبايل
try {
    const antiPullStyle = document.createElement("style");
    antiPullStyle.textContent = "html, body { overscroll-behavior-y: none !important; }";
    document.head && document.head.appendChild(antiPullStyle);
} catch (_) {}

// ---------------------------------------------------------------
// الدالة الأساسية للـ HTTP requests
// ---------------------------------------------------------------
async function _request(method, endpoint, body = null, isFormData = false) {
  const token = localStorage.getItem("access_token");
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const adminProof = getAdminAppProof();
  if (adminProof) headers["X-Admin-App-Proof"] = adminProof;
  const devId = getDeviceID();
  if (devId) headers["X-Device-Serial"] = devId;
  if (!isFormData && body) headers["Content-Type"] = "application/json";

  const config = {
    method,
    headers,
    body: isFormData ? body : (body ? JSON.stringify(body) : null),
  };

  try {
    const res  = await fetch(`${BASE_URL}${endpoint}`, config);
    const data = await res.json().catch(() => null);

    if (
      res.status === 401 &&
      !endpoint.includes("/auth/login") &&
      !endpoint.includes("/admin-vault/login") &&
      !endpoint.includes("/auth/admin/recover") &&
      !endpoint.includes("/auth/teacher-login") &&
      !endpoint.includes("/sender/login")
    ) {
      localStorage.removeItem("access_token");
      localStorage.removeItem("current_user");
      window.location.href = "login.html";
      return;
    }

    if (!res.ok) {
      const detail = data?.detail;
      const msg = Array.isArray(detail)
        ? detail.map(e => `${e.loc?.slice(-1)?.[0] || ''}: ${e.msg}`).join(' | ')
        : (detail || `خطأ ${res.status}`);
      throw new Error(msg);
    }
    return data;
  } catch (err) {
    if (err.message.includes("Failed to fetch")) {
      throw new Error("تعذر الاتصال بالسيرفر. تأكد إن الـ Backend شغال.");
    }
    throw err;
  }
}

const get    = (url)         => _request("GET",    url);
const post   = (url, body)   => _request("POST",   url, body);
const patch  = (url, body)   => _request("PATCH",  url, body);
const put    = (url, body)   => _request("PUT",    url, body);
const del    = (url)         => _request("DELETE", url);
const upload = (url, form)   => _request("POST",   url, form, true);
const uploadPatch = (url, form) => _request("PATCH", url, form, true);

// ================================================================
// APIs
// ================================================================

const authAPI = {
  register: (data) => post("/auth/register", data),
  login: async (email, password) => {
    const deviceId = getDeviceID();
    const res = await post("/auth/login", { email, password, device_id: deviceId });
    localStorage.setItem("access_token",  res.access_token);
    localStorage.setItem("current_user",  JSON.stringify(res.user));
    return res;
  },
  logout: () => {
    localStorage.removeItem("access_token");
    localStorage.removeItem("current_user");
    window.location.href = "login.html";
  },
  me: () => get("/auth/me"),
};

const usersAPI = {
  updateMe: (data) => put("/users/me", data),
  uploadAvatar: (formData) => upload("/users/me/avatar", formData),
  uploadWatermarkLogo: (formData) => upload("/users/me/watermark-logo", formData),
  getNotifications: () => get("/users/notifications"),
  markNotificationsRead: () => post("/users/notifications/read", {}),
  getPreferences: () => get("/users/me/preferences"),
  updatePreferences: (data) => patch("/users/me/preferences", { preferences: data }),
};

const chatAPI = {
  getMessages: (limit = 35) => get(`/chat/messages?limit=${limit}`),
  getWsUrl: () => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    let host = window.location.host;
    if (isLiveServer) {
      host = `${window.location.hostname}:8000`;
    }
    return `${protocol}//${host}/api/chat/ws`;
  }
};

const adminAPI = {
  resetPassword: (userId, newPassword) => put(`/users/admin/${userId}/password?new_password=${encodeURIComponent(newPassword)}`),
  deleteUser: (userId) => del(`/users/admin/${userId}`),
  getAudit: (userId) => get(`/users/admin/${userId}/audit`),
  getUserDevices: (userId) => get(`/users/admin/${userId}/devices`),
  resetUserDevices: (userId) => del(`/users/admin/${userId}/devices`),
  toggleDeviceBlock: (recordId) => post(`/users/admin/devices/${recordId}/toggle-block`, {}),
  getSpendingLevels: () => get("/coins/admin/spending-levels"),
  createSpendingLevel: (data) => post("/coins/admin/spending-levels", data),
  deleteSpendingLevel: (id) => del(`/coins/admin/spending-levels/${id}`),
};

const materialsAPI = {
  list: (page = 1, perPage = 12) => get(`/materials/?page=${page}&per_page=${perPage}&t=${Date.now()}`),
  listAll: (page = 1) => get(`/materials/admin/all?page=${page}&t=${Date.now()}`),
  get: (id) => get(`/materials/${id}`),
  upload: (formData) => upload("/materials/", formData),
  update: (id, data) => {
    if (data instanceof FormData) {
      return uploadPatch(`/materials/${id}`, data);
    }
    const formData = new FormData();
    for (const key in data) {
      if (data[key] !== undefined && data[key] !== null) {
        // Convert boolean to explicit string to ensure FastAPI Form() parses it correctly
        const val = typeof data[key] === 'boolean' ? String(data[key]) : data[key];
        formData.append(key, val);
      }
    }
    return uploadPatch(`/materials/${id}`, formData);
  },
  delete: (id) => del(`/materials/${id}`),
  getPreviewToken: (materialId, pagesAllowed = 5) =>
    post(`/materials/${materialId}/preview-token`, { material_id: materialId, pages_allowed: pagesAllowed }),
  getPreviewPage: async (token, page = 1) => {
    const res = await fetch(`${BASE_URL}/materials/preview/stream?token=${token}&page=${page}`, {
      headers: { "Authorization": `Bearer ${localStorage.getItem("access_token")}` }
    });
    if (!res.ok) throw new Error("فشل تحميل صفحة المعاينة");
    const data = await res.json();
    const binaryString = atob(data.pdf_64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) bytes[i] = binaryString.charCodeAt(i);
    return bytes.buffer;
  },
  getReviews: (id) => get(`/materials/${id}/reviews`),
  submitReview: (id, data) => post(`/materials/${id}/reviews`, data),
};

const layoutsAPI = {
  list: (materialId) => get(`/layouts/?material_id=${materialId}`),
  create: (data) => post("/layouts/", data),
  update: (id, data) => patch(`/layouts/${id}`, data),
  trackLeak: (markCode) => get(`/layouts/leaks/track?mark_code=${encodeURIComponent(markCode)}`),
};

const presetsAPI = {
  list: () => get("/presets/"),
  create: (data) => post("/presets/", data),
  delete: (id) => del(`/presets/${id}`),
};

const coinsAPI = {
  history: () => get("/coins/history"),
  recharge: (userId, amount, description) => post(`/coins/admin/recharge/${userId}`, { amount, description }),
  listUsers: (page = 1) => get(`/coins/admin/users?page=${page}`),
  redeemCoupon: (code) => post(`/coins/redeem-coupon?code=${encodeURIComponent(code)}`, {}),
  rechargeWallet: (transferPhone) => post(`/coins/recharge-wallet?transfer_phone=${encodeURIComponent(transferPhone)}`, {}),
};

const purchasesAPI = {
  create: (data) => post("/purchases/", data),
  walletAutoPay: (data) => post("/purchases/wallet-auto", data),
  myPurchases: (page = 1, per_page = 20, search = "") => 
    get(`/purchases/?page=${page}&per_page=${per_page}${search ? `&search=${encodeURIComponent(search)}` : ''}`),
  get: (id) => get(`/purchases/${id}`),
  prepareDownload: (id) => post(`/purchases/${id}/prepare-download`, {}),
  requestDownloadToken: (purchaseId) => post(`/purchases/${purchaseId}/download-token`, {}),
  repairDownload: (id) => post(`/purchases/repair/${id}`),
  download: (token) => `${BASE_URL}/purchases/download?token=${token}`,
  delete: (id) => del(`/purchases/${id}`),
};

const jobsAPI = {
  startTestDownload: (materialId, stampElements) =>
    post(`/materials/${materialId}/designer-test-start`, { material_id: parseInt(materialId), stamp_elements: stampElements }),
  status: (jobId) => get(`/jobs/${jobId}`),
  downloadUrl: (jobId) => `${BASE_URL}/jobs/${jobId}/download`,
  recent: () => get("/jobs/recent"),
};

const statsAPI = {
  getDashboard: () => get("/stats/dashboard"),
};

const v3API = {
  // Sovereign Admin Vault (Password-Only)
  adminVaultStatus: () => get(`/v3/admin-vault/status?device_id=${encodeURIComponent(getDeviceID())}&t=${Date.now()}`),
  adminSetupFounding: async (data) => {
    const res = await post("/v3/admin-vault/setup-founding", { ...data, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  adminVaultLogin: async (password) => {
    const res = await post("/v3/admin-vault/login", { password, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  requestCoAdmin: (data) => post("/v3/admin-vault/request-co-admin", { ...data, device_id: getDeviceID() }),
  pollCoAdmin: (userId) => get(`/v3/admin-vault/poll-co-admin?user_id=${userId}&device_id=${encodeURIComponent(getDeviceID())}`),
  manageCoAdmin: (userId, action) => post(`/v3/admin-vault/co-admins/${userId}/action`, typeof action === "string" ? { action } : action),
  changeMasterPassword: (newPassword) => post("/v3/admin-vault/change-master-password", { new_password: newPassword, device_id: getDeviceID() }),

  // OTP Engine
  requestOtp: (data) => post("/v3/otp/request", data),
  pollOtpDelivery: (challengeToken) => get(`/v3/otp/poll-delivery?challenge_token=${encodeURIComponent(challengeToken)}&t=${Date.now()}`),

  // Teacher v3.1 Auth (Phone + Password + OTP)
  teacherRegister: async (data) => {
    const res = await post("/v3/auth/teacher-register", { ...data, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  teacherLogin: async (data) => {
    const res = await post("/v3/auth/teacher-login", { ...data, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  teacherResetPassword: async (data) => {
    const res = await post("/v3/auth/teacher-reset-password", { ...data, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  teacherActivateSerial: async (data) => {
    const res = await post("/v3/auth/teacher-activate-serial", { ...data, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
  teacherStatus: (phone) => get(`/v3/auth/teacher-status?phone=${encodeURIComponent(phone)}&device_id=${encodeURIComponent(getDeviceID())}`),

  // Sender v3.1 Auth (Phone + Password + OTP)
  senderRegister: (data) => post("/v3/sender/register", { ...data, device_id: getDeviceID() }),
  senderLogin: (data) => post("/v3/sender/login", { ...data, device_id: getDeviceID() }),
  senderResetPassword: (data) => post("/v3/sender/reset-password", { ...data, device_id: getDeviceID() }),
  senderStatus: (phone) => get(`/v3/sender/status?phone=${encodeURIComponent(phone)}&device_id=${encodeURIComponent(getDeviceID())}`),

  // Backwards compatible quick teacher helper
  teacherQuickAccess: async (payload) => {
    const res = await post("/v3/auth/teacher-quick", { ...payload, device_id: getDeviceID() });
    if (res && res.status === "approved" && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },

  // Admin and Senders Operations (both alias styles for bulletproof compatibility)
  adminOverview: () => get(`/v3/admin/overview?t=${Date.now()}`),
  manageSender: (senderId, data) => post(`/v3/admin/senders/${senderId}/action`, typeof data === "string" ? { action: data } : data),
  adminSenderAction: (senderId, data) => post(`/v3/admin/senders/${senderId}/action`, typeof data === "string" ? { action: data } : data),
  settleSender: (senderId, data) => post(`/v3/admin/senders/${senderId}/settle`, data),
  adminSettleSender: (senderId, data) => post(`/v3/admin/senders/${senderId}/settle`, data),
  manageTeacher: (userId, data) => post(`/v3/admin/teachers/${userId}/action`, typeof data === "string" ? { action: data } : data),
  adminTeacherAction: (userId, data) => post(`/v3/admin/teachers/${userId}/action`, typeof data === "string" ? { action: data } : data),
  generateSerials: (data) => post("/v3/admin/serials/generate", data),
  generateVipSerials: (data) => post("/v3/admin/serials/generate", data),
  bundleAction: (targetCode, data) => post(`/v3/admin/bundles/${encodeURIComponent(targetCode)}/action`, typeof data === "string" ? { action: data } : data),
  adminBundleAction: (targetCode, data) => post(`/v3/admin/bundles/${encodeURIComponent(targetCode)}/action`, typeof data === "string" ? { action: data } : data),
  setMaterialCommission: (materialId, senderCommission) => put(`/v3/admin/materials/${materialId}/sender-commission`, { sender_commission: senderCommission }),
  publishAppUpdate: (data) => post("/v3/admin/app-updates/publish", data),
  checkAppUpdate: (flavor, versionCode) => get(`/app-updates/check?flavor=${encodeURIComponent(flavor)}&version_code=${versionCode}`),
  checkoutBundle: (formData) => upload("/v3/bundles/checkout", formData),
};

// HELPERS
function getCurrentUser() {
  const data = localStorage.getItem("current_user");
  return data ? JSON.parse(data) : null;
}
function isLoggedIn() { return !!localStorage.getItem("access_token"); }
function isAdmin() { return getCurrentUser()?.role === "admin"; }
function requireAuth(adminOnly = false) {
  if (!isLoggedIn()) { window.location.href = "login.html"; return false; }
  if (adminOnly && !isAdmin()) { window.location.href = "store.html"; return false; }
  return true;
}

window.authAPI = authAPI;
window.usersAPI = usersAPI;
window.adminAPI = adminAPI;
window.chatAPI = chatAPI;
window.materialsAPI = materialsAPI;
window.layoutsAPI = layoutsAPI;
window.purchasesAPI = purchasesAPI;
window.presetsAPI = presetsAPI;
window.coinsAPI = coinsAPI;
window.jobsAPI = jobsAPI;
window.statsAPI = statsAPI;
const adminSecurityAPI = {
  getOverview: () => get("/auth/admin/security"),
  updateMaxDevices: (maxDevices, kickExcess = true) =>
    put("/auth/admin/security/max-devices", { max_devices: Number(maxDevices), kick_excess: Boolean(kickExcess) }),
  saveRecoveryPassword: (payload) =>
    post("/auth/admin/security/recovery-password", payload),
  terminateSession: (sessionId, releaseDevice = true, blockDevice = false) =>
    del(`/auth/admin/security/sessions/${encodeURIComponent(sessionId)}?release_device=${Boolean(releaseDevice)}&block_device=${Boolean(blockDevice)}`),
  revokeOtherSessions: (releaseDevices = true) =>
    post(`/auth/admin/security/sessions/revoke-others?release_devices=${Boolean(releaseDevices)}`, {}),
  removeDevice: (deviceRowId) =>
    del(`/auth/admin/security/devices/${deviceRowId}`),
  toggleBlockDevice: (deviceRowId) =>
    post(`/auth/admin/security/devices/${deviceRowId}/toggle-block`, {}),
  recoverAccount: async (recoveryPassword, newPassword) => {
    const res = await post("/auth/admin/recover", {
      recovery_password: recoveryPassword,
      new_password: newPassword,
      device_id: getDeviceID(),
    });
    if (res && res.access_token) {
      localStorage.setItem("access_token", res.access_token);
      localStorage.setItem("current_user", JSON.stringify(res.user));
    }
    return res;
  },
};

window.v3API = v3API;
window.adminSecurityAPI = adminSecurityAPI;
window.getCurrentUser = getCurrentUser;
window.isLoggedIn = isLoggedIn;
window.isAdmin = isAdmin;
window.requireAuth = requireAuth;

// Global Preferences Manager
window.loadUserPreferences = async function() {
    if (!isLoggedIn()) return;
    
    // 1. Try to load from localStorage first for instant rendering
    try {
        const cachedPrefs = localStorage.getItem("user_preferences");
        if (cachedPrefs) {
            applyPreferencesToDOM(JSON.parse(cachedPrefs));
        }
    } catch (e) {}

    // 2. Fetch fresh from backend (sync across devices)
    try {
        const prefs = await usersAPI.getPreferences();
        localStorage.setItem("user_preferences", JSON.stringify(prefs));
        applyPreferencesToDOM(prefs);
    } catch (e) {
        console.error("Failed to load user preferences", e);
    }
};

function applyPreferencesToDOM(prefs) {
    if (!prefs) return;
    
    // Theme
    if (prefs.theme === "light") {
        document.body.classList.remove("dark-theme");
        document.body.classList.add("light-theme");
    } else {
        document.body.classList.remove("light-theme");
        document.body.classList.add("dark-theme");
    }

    // Chat Visibility
    const chatWidget = document.getElementById("chat-widget");
    if (chatWidget) {
        if (prefs.chat_visible === false) {
            chatWidget.style.display = "none";
        } else {
            chatWidget.style.display = "flex";
        }
    }

    // Notifications (Optional, can be checked by specific components)
    window.USER_PREFS = prefs;
}
window.applyPreferencesToDOM = applyPreferencesToDOM;

// Auto-run on load
document.addEventListener("DOMContentLoaded", () => {
    window.loadUserPreferences();
});
