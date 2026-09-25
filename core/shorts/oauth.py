from __future__ import annotations
import secrets, hashlib, base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from bot.config import Settings
from bot.database.models import ShortsOAuthState, YouTubeAccount
from bot.database.session import get_session
from sqlalchemy import select, delete
SCOPES=["https://www.googleapis.com/auth/youtube.upload","https://www.googleapis.com/auth/youtube.readonly"]
class YouTubeOAuth:
    def __init__(self, settings: Settings): self.settings=settings
    def _client_config(self)->dict:
        if self.settings.youtube_client_id and self.settings.youtube_client_secret:
            return {"web":{"client_id":self.settings.youtube_client_id,"client_secret":self.settings.youtube_client_secret,"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}}
        p=self.settings.youtube_client_secrets
        if not p or not Path(p).exists(): raise RuntimeError("Configure YOUTUBE_CLIENT_ID/YOUTUBE_CLIENT_SECRET or YOUTUBE_CLIENT_SECRETS")
        import json
        raw=json.loads(Path(p).read_text())
        return raw if "web" in raw else {"web":raw["installed"]}
    def _fernet(self)->Fernet:
        key=self.settings.shorts_oauth_encryption_key
        if not key: raise RuntimeError("SHORTS_OAUTH_ENCRYPTION_KEY is required")
        return Fernet(key.encode())
    async def authorization_url(self,user_id:int)->str:
        from google_auth_oauthlib.flow import Flow
        state=secrets.token_urlsafe(32)
        verifier=secrets.token_urlsafe(64)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        flow=Flow.from_client_config(self._client_config(),scopes=SCOPES)
        flow.redirect_uri=self.settings.youtube_oauth_redirect_uri
        url,_=flow.authorization_url(access_type="offline",include_granted_scopes="true",prompt="consent",state=state,code_challenge=challenge,code_challenge_method="S256")
        async with get_session() as s:
            s.add(ShortsOAuthState(state=state,user_id=user_id,code_verifier=verifier,expires_at=datetime.now(timezone.utc)+timedelta(seconds=self.settings.shorts_oauth_state_ttl_seconds)))
        return url
    async def handle_callback(self,state:str,code:str)->YouTubeAccount:
        from google_auth_oauthlib.flow import Flow
        async with get_session() as s:
            q=await s.execute(select(ShortsOAuthState).where(ShortsOAuthState.state==state))
            st=q.scalar_one_or_none()
            if not st or st.expires_at.replace(tzinfo=timezone.utc)<datetime.now(timezone.utc): raise RuntimeError("OAuth state expired or invalid")
            await s.delete(st)
            flow=Flow.from_client_config(self._client_config(),scopes=SCOPES,state=state)
            flow.redirect_uri=self.settings.youtube_oauth_redirect_uri
            flow.fetch_token(code=code,code_verifier=st.code_verifier)
            creds=flow.credentials
            if not creds.refresh_token: raise RuntimeError("Google did not return a refresh token; reconnect with consent")
            from googleapiclient.discovery import build
            yt=build("youtube","v3",credentials=creds,cache_discovery=False)
            data=yt.channels().list(part="id,snippet",mine=True).execute()
            items=data.get("items") or []
            if not items: raise RuntimeError("No authenticated YouTube channel was found")
            ch=items[0]
            token=self._fernet().encrypt(creds.refresh_token.encode()).decode()
            q2=await s.execute(select(YouTubeAccount).where(YouTubeAccount.user_id==st.user_id,YouTubeAccount.channel_id==ch["id"]))
            account=q2.scalar_one_or_none()
            if account: account.channel_title=ch["snippet"]["title"]; account.encrypted_refresh_token=token; account.scopes=list(creds.scopes or SCOPES); account.revoked_at=None
            else: account=YouTubeAccount(user_id=st.user_id,channel_id=ch["id"],channel_title=ch["snippet"]["title"],encrypted_refresh_token=token,scopes=list(creds.scopes or SCOPES)); s.add(account)
            await s.flush()
            return account
    def credentials(self,account:YouTubeAccount):
        from google.oauth2.credentials import Credentials
        refresh=self._fernet().decrypt(account.encrypted_refresh_token.encode()).decode()
        return Credentials(token=None,refresh_token=refresh,token_uri="https://oauth2.googleapis.com/token",client_id=self._client_config()["web"]["client_id"],client_secret=self._client_config()["web"]["client_secret"],scopes=SCOPES)
    async def disconnect(self,account_id:int,user_id:int)->None:
        async with get_session() as s:
            q=await s.execute(select(YouTubeAccount).where(YouTubeAccount.id==account_id,YouTubeAccount.user_id==user_id))
            a=q.scalar_one_or_none()
            if not a:return
            try:
                import httpx
                refresh=self._fernet().decrypt(a.encrypted_refresh_token.encode()).decode()
                await httpx.AsyncClient(timeout=20).post("https://oauth2.googleapis.com/revoke",params={"token":refresh})
            except Exception: pass
            a.revoked_at=datetime.now(timezone.utc); a.encrypted_refresh_token=""
