# ================================================================
# bot_service/services/bridge_client.py — عميل الجسر الداخلي
# ================================================================

import logging
from typing import Optional, Dict, Any, List
import httpx

from bot_service.config import bot_config

logger = logging.getLogger("bot_service.bridge_client")


class BridgeClient:
    """
    عميل اتصال غير متزامن فائق السرعة بمخارج الـ Internal Bridge
    الخاصة بمنصة Z-PACT الأساسية.
    """
    def __init__(self):
        self.base_url = bot_config.CORE_API_URL.rstrip('/')
        self.headers = {
            "X-Internal-Service-Key": bot_config.INTERNAL_SERVICE_KEY,
            "Content-Type": "application/json"
        }

    # -------------------------------------------------------------
    # 1. كتالوج المذكرات والمواد
    # -------------------------------------------------------------
    async def get_materials(
        self,
        tag: Optional[str] = None,
        search: Optional[str] = None,
        include_unpublished: bool = False,
    ) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/internal/materials"
        params = {}
        if tag:
            params["tag"] = tag
        if search:
            params["search"] = search
        if include_unpublished:
            params["include_unpublished"] = "true"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers, params=params)
                if res.status_code == 200:
                    return res.json().get("materials", [])
                logger.error(f"❌ [Bridge] Failed to fetch materials: {res.status_code} - {res.text}")
                return []
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_materials: {e}")
                return []

    # -------------------------------------------------------------
    # 2. الملف التعريفي للمدرس
    # -------------------------------------------------------------
    async def get_teacher_profile(self, telegram_user_id: int | str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/teachers/profile"
        params = {"telegram_user_id": str(telegram_user_id)}

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers, params=params)
                if res.status_code == 200:
                    return res.json()
                elif res.status_code == 404:
                    return None
                logger.error(f"❌ [Bridge] get_teacher_profile ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_teacher_profile: {e}")
                return None

    async def upsert_teacher_profile(self, profile_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/teachers/profile"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=profile_data)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] upsert_teacher_profile ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] upsert_teacher_profile: {e}")
                return None

    # -------------------------------------------------------------
    # 3. جلسات البوت (FSM Sync)
    # -------------------------------------------------------------
    async def sync_session(
        self,
        telegram_chat_id: int | str,
        telegram_user_id: int | str,
        current_state: Optional[str] = None,
        state_data: Optional[dict] = None,
        username: Optional[str] = None
    ) -> bool:
        url = f"{self.base_url}/internal/sessions/sync"
        payload = {
            "telegram_chat_id": str(telegram_chat_id),
            "telegram_user_id": str(telegram_user_id),
            "current_state": current_state,
            "state_data": state_data or {},
            "username": username
        }

        async with httpx.AsyncClient(timeout=6.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                return res.status_code == 200
            except Exception as e:
                logger.warning(f"⚠️ [Bridge] sync_session silent fail: {e}")
                return False

    async def get_session(self, telegram_chat_id: int | str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/sessions/{telegram_chat_id}"

        async with httpx.AsyncClient(timeout=6.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json()
                return None
            except Exception:
                return None

    # -------------------------------------------------------------
    # 4. إدارة ومطابقة الطلبات
    # -------------------------------------------------------------
    async def create_order(self, order_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/orders/create"

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=order_data)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] create_order ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] create_order: {e}")
                return None

    async def get_order_status(self, order_code: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/orders/{order_code}/status"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json()
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_order_status: {e}")
                return None

    async def get_user_orders(self, chat_id: int | str) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/internal/orders/user/{chat_id}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json()
                return []
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_user_orders: {e}")
                return []


    async def admin_order_action(self, order_code: str, action: str, admin_notes: Optional[str] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/orders/{order_code}/admin-action"
        payload = {"action": action, "admin_notes": admin_notes}

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] admin_order_action ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] admin_order_action: {e}")
                return None

    async def download_order_pdf(self, order_code: str) -> Optional[bytes]:
        url = f"{self.base_url}/internal/orders/{order_code}/download"

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.content
                logger.error(f"❌ [Bridge] download_order_pdf ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] download_order_pdf: {e}")
                return None

    # -------------------------------------------------------------
    # 5. لوحة تحكم الأدمن والرسائل
    # -------------------------------------------------------------
    async def get_admin_stats(self) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/stats"

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json()
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_admin_stats: {e}")
                return None

    async def reconcile_manual(
        self,
        raw_message: str,
        source_provider: str,
        amount: float,
        sender: str,
        ref_num: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/webhook/reconcile"
        payload = {
            "raw_message": raw_message,
            "source_provider": source_provider,
            "extracted_amount": amount,
            "extracted_sender": sender,
            "reference_number": ref_num
        }

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                if res.status_code == 200:
                    return res.json()
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] reconcile_manual: {e}")
                return None

    # -------------------------------------------------------------
    # 6. إدارة المذكرات والأسعار ورفع الـ PDF للأدمن
    # -------------------------------------------------------------
    async def update_material(self, material_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.put(url, headers=self.headers, json=data)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] update_material: {e}")
                return None

    async def toggle_material_publish(self, material_id: int) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/toggle-publish"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, headers=self.headers)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] toggle_material_publish: {e}")
                return None

    async def delete_material(self, material_id: int) -> bool:
        url = f"{self.base_url}/internal/admin/materials/{material_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.delete(url, headers=self.headers)
                return res.status_code == 200
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] delete_material: {e}")
                return False

    async def upload_material(
        self,
        file_bytes: bytes,
        filename: str,
        title: str,
        price: float,
        tag: Optional[str] = None,
        description: Optional[str] = None,
        cover_bytes: Optional[bytes] = None,
        cover_filename: str = "cover.png",
        frame_bytes: Optional[bytes] = None,
        frame_filename: str = "frame.png",
        font_family: str = "cairo",
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/upload"
        headers = {"X-Internal-Service-Key": bot_config.INTERNAL_SERVICE_KEY}
        files = [("file", (filename, file_bytes, "application/pdf"))]
        if cover_bytes:
            files.append(("cover_image", (cover_filename, cover_bytes, "image/png")))
        if frame_bytes:
            files.append(("page_frame", (frame_filename, frame_bytes, "image/png")))
        data = {
            "title": title,
            "price": str(price),
            "tag": tag or "مذكرات عامة",
            "description": description or f"مذكرة {title}",
            "font_family": font_family or "cairo",
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            try:
                res = await client.post(url, headers=headers, data=data, files=files)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] upload_material ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] upload_material: {e}")
                return None

    async def get_broadcast_recipients(self) -> List[int]:
        url = f"{self.base_url}/internal/admin/broadcast-recipients"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json().get("chat_ids", [])
                return []
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_broadcast_recipients: {e}")
                return []

    # -------------------------------------------------------------
    # 7. منظومة الإصدار الثالث V3 (المرسلون، السيريالات، السلال المجمعة، التحديثات)
    # -------------------------------------------------------------
    async def get_v3_admin_overview(self) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin/overview"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_v3_admin_overview: {e}")
                return None

    async def manage_sender(self, sender_id: int, action: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin/senders/{sender_id}/action"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json={"action": action})
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] manage_sender: {e}")
                return None

    async def manage_teacher(self, user_id: int, action: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin/teachers/{user_id}/action"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json={"action": action})
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] manage_teacher: {e}")
                return None

    async def manage_bundle(self, target_code: str, action: str, reason: str = "") -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin/bundles/{target_code}/action"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json={"action": action, "reason": reason})
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] manage_bundle: {e}")
                return None

    async def publish_ota_update(self, flavor: str = "all", release_notes: str = "") -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin/app-updates/publish"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.post(
                    url,
                    headers=self.headers,
                    json={"flavor": flavor, "release_notes": release_notes},
                )
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] publish_ota_update: {e}")
                return None

    async def checkout_bundle_multipart(
        self,
        fields: Dict[str, str],
        receipt_bytes: Optional[bytes] = None,
        receipt_filename: str = "receipt.jpg",
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/bundles/checkout"
        headers = {"X-Internal-Service-Key": bot_config.INTERNAL_SERVICE_KEY}
        files = {"receipt_image": (receipt_filename, receipt_bytes, "image/jpeg")} if receipt_bytes else None
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(url, headers=headers, data=fields, files=files)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] checkout_bundle_multipart ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] checkout_bundle_multipart: {e}")
                return None


    # -------------------------------------------------------------
    # 8. منظومة التحقق OTP وحوكمة المشرفين V3.1
    # -------------------------------------------------------------
    async def claim_telegram_otp(
        self,
        challenge_token: str,
        telegram_user_id: int | str,
        telegram_username: Optional[str] = None,
        telegram_phone: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/otp/claim-telegram"
        payload = {
            "challenge_token": challenge_token,
            "telegram_user_id": str(telegram_user_id),
            "telegram_username": telegram_username,
            "telegram_phone": telegram_phone,
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                if res.status_code == 200:
                    return res.json()
                logger.error(f"❌ [Bridge] claim_telegram_otp ({res.status_code}): {res.text}")
                return None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] claim_telegram_otp: {e}")
                return None

    async def manage_co_admin(self, user_id: int, action: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/v3/admin-vault/co-admins/{user_id}/action"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json={"action": action})
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] manage_co_admin: {e}")
                return None

    # -------------------------------------------------------------
    # 9. استوديو الخطوط والإطارات والإحداثيات والدفعات من البوت
    # -------------------------------------------------------------
    async def get_fonts(self) -> List[Dict[str, str]]:
        url = f"{self.base_url}/internal/admin/fonts"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                if res.status_code == 200:
                    return res.json().get("fonts", [])
                return []
            except Exception:
                return []

    async def get_material_layout(self, material_id: int) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/layout"
        async with httpx.AsyncClient(timeout=12.0) as client:
            try:
                res = await client.get(url, headers=self.headers)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_material_layout: {e}")
                return None

    async def save_material_layout(
        self,
        material_id: int,
        stamp_elements: List[Dict[str, Any]],
        layout_type: str = "default_text",
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/layout"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.put(
                    url,
                    headers=self.headers,
                    json={"layout_type": layout_type, "stamp_elements": stamp_elements},
                )
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] save_material_layout: {e}")
                return None

    async def quick_font_adjust(self, material_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/quick-font-adjust"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] quick_font_adjust: {e}")
                return None

    async def upload_material_frame(
        self,
        material_id: int,
        frame_bytes: bytes,
        frame_filename: str = "frame.png",
    ) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/frame"
        headers = {"X-Internal-Service-Key": bot_config.INTERNAL_SERVICE_KEY}
        files = {"frame_image": (frame_filename, frame_bytes, "image/png")}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.post(url, headers=headers, files=files)
                return res.json() if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] upload_material_frame: {e}")
                return None

    async def delete_material_frame(self, material_id: int) -> bool:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/frame"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.delete(url, headers=self.headers)
                return res.status_code == 200
            except Exception:
                return False

    async def get_preview_page_png(
        self,
        material_id: int,
        page: int = 1,
        stamped: int = 1,
        zoom: float = 1.35,
    ) -> Optional[bytes]:
        url = f"{self.base_url}/internal/admin/materials/{material_id}/preview-page"
        params = {"page": page, "stamped": stamped, "zoom": zoom}
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                res = await client.get(url, headers=self.headers, params=params)
                return res.content if res.status_code == 200 else None
            except Exception as e:
                logger.error(f"❌ [Bridge Exception] get_preview_page_png: {e}")
                return None

    async def direct_stamp_sync(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/internal/orders/direct-stamp-sync"
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                res = await client.post(url, headers=self.headers, json=payload)
                data = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
                if res.status_code == 200:
                    return {"ok": True, "data": data}
                return {"ok": False, "error": data.get("detail") or f"HTTP {res.status_code}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}


bridge_client = BridgeClient()
