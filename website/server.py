"""샤벳 홈페이지 로컬 OAuth 연결. 운영 봇/설정 파일에는 접근하지 않습니다."""
import asyncio
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

ORIGIN = "http://127.0.0.1:8766"
CALLBACK = ORIGIN + "/auth/callback"
API = "https://discord.com/api/v10"
ROOT = Path(__file__).parent / "dist"


def redirect(location):
    return web.Response(status=302, headers={"Location": location})


def is_admin(guild):
    # 봇의 /채널설정과 동일하게 관리자 또는 서버 소유자만 표시합니다.
    try:
        return guild.get("owner") is True or bool(int(guild.get("permissions", 0)) & 8)
    except (TypeError, ValueError):
        return False


def create_app(client_id="", client_secret="", discord_request=None, *, guild_snapshot=None):
    sessions, pending = {}, {}

    async def call(method, path, *, token=None, data=None):
        if discord_request:
            return await discord_request(method, path, token=token, data=data)
        # 인증 코드를 로그나 브라우저에 노출하지 않고 서버에서 교환합니다.
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as http:
            async with http.request(method, API + path, data=data,
                                    headers={"Authorization": "Bearer " + token} if token else {}) as res:
                if res.status != 200:
                    raise ValueError("Discord request failed")
                return await res.json()

    @web.middleware
    async def safety(request, handler):
        # 로컬 연결 전용: 다른 Host와 교차 출처 요청을 허용하지 않습니다.
        if request.host != "127.0.0.1:8766":
            return web.json_response({"error": "허용되지 않은 주소입니다."}, status=403)
        now = time.monotonic()
        for table in (sessions, pending):
            for key in list(table):
                if table[key]["until"] <= now:
                    table.pop(key, None)
        try:
            response = await handler(request)
        except web.HTTPException as error:
            response = web.Response(status=error.status, text=error.text, headers=error.headers)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
            response = web.json_response({"error": "Discord 연결에 실패했습니다. 잠시 후 다시 로그인해주세요."}, status=502)
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"})
        return response

    def session(request):
        result = sessions.get(request.cookies.get("sherbet_session", ""))
        if not result:
            raise web.HTTPUnauthorized(text="다시 로그인해주세요.")
        return result

    async def status(request):
        item = sessions.get(request.cookies.get("sherbet_session", ""))
        return web.json_response({"ready": bool(client_id and client_secret), "callback": CALLBACK,
                                  "user": item["user"] if item else None,
                                  "csrf": item["csrf"] if item else None})

    async def login(request):
        if not client_id or not client_secret:
            return redirect("/#account")
        if len(pending) >= 1000:
            raise web.HTTPTooManyRequests()
        binding, state = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        pending.pop(request.cookies.get("sherbet_login", ""), None)
        pending[binding] = {"state": state, "until": time.monotonic() + 600}
        response = redirect("https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": client_id, "redirect_uri": CALLBACK, "response_type": "code",
            "scope": "identify guilds", "state": state}))
        response.set_cookie("sherbet_login", binding, httponly=True, samesite="Lax", max_age=600, path="/auth")
        return response

    async def callback(request):
        # 브라우저별 일회용 state가 맞아야 코드 교환을 시작합니다.
        item = pending.pop(request.cookies.get("sherbet_login", ""), None)
        state = request.query.get("state", "")
        if not item or not secrets.compare_digest(item["state"], state):
            raise web.HTTPBadRequest(text="로그인 요청이 만료됐습니다. 홈페이지에서 다시 로그인해주세요.")
        if request.query.get("error"):
            return redirect("/#account")
        code = request.query.get("code")
        if not code:
            raise web.HTTPBadRequest(text="로그인 코드가 없습니다.")
        if len(sessions) >= 1000:
            raise web.HTTPTooManyRequests()
        result = await call("POST", "/oauth2/token", data={"client_id": client_id,
                            "client_secret": client_secret, "grant_type": "authorization_code",
                            "code": code, "redirect_uri": CALLBACK})
        token = result["access_token"]
        user = await call("GET", "/users/@me", token=token)
        sid = secrets.token_urlsafe(32)
        # 토큰은 메모리에만 보관하며 재시작/한 시간 후 다시 로그인합니다.
        sessions.pop(request.cookies.get("sherbet_session", ""), None)
        sessions[sid] = {"token": token, "user": {"id": user["id"], "name": user.get("global_name") or user["username"]},
                         "csrf": secrets.token_urlsafe(32),
                         "until": time.monotonic() + min(3600, max(0, int(result["expires_in"])))}
        response = redirect("/#account")
        response.del_cookie("sherbet_login", path="/auth")
        response.set_cookie("sherbet_session", sid, httponly=True, samesite="Lax", max_age=3600)
        return response

    async def admin_guilds(item):
        rows = []
        after = "0"
        # 서버가 많은 계정도 누락하지 않도록 Discord 페이지 단위로 조회합니다.
        for _ in range(10):
            batch = await call("GET", "/users/@me/guilds?" + urlencode({"limit": 200, "after": after}), token=item["token"])
            rows.extend({"id": g["id"], "name": g["name"]} for g in batch if is_admin(g))
            if len(batch) < 200:
                return rows
            after = str(max(int(g["id"]) for g in batch))
        raise web.HTTPBadGateway(text="서버 목록을 모두 확인하지 못했습니다.")

    async def guilds(request):
        rows = await admin_guilds(session(request))
        return web.json_response({"guilds": rows, "settingsConnected": guild_snapshot is not None})

    async def guild_detail(request):
        item = session(request)
        gid = request.match_info['guild_id']
        if not gid.isascii() or not gid.isdigit() or len(gid) > 20:
            raise web.HTTPBadRequest(text="잘못된 서버입니다.")
        # 목록을 받은 뒤 관리자 권한이 사라졌을 수 있어 요청마다 재확인합니다.
        if not any(g['id'] == gid for g in await admin_guilds(item)):
            raise web.HTTPForbidden(text="이 서버의 관리자 권한을 확인할 수 없습니다.")
        if guild_snapshot is None:
            raise web.HTTPServiceUnavailable(text="로그인은 연결됐지만 운영 봇 조회는 아직 연결되지 않았습니다.")
        return web.json_response(await guild_snapshot(int(gid)))

    async def logout(request):
        item = session(request)
        if request.headers.get("Origin") != ORIGIN or not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), item["csrf"]):
            raise web.HTTPForbidden()
        sessions.pop(request.cookies.get("sherbet_session", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("sherbet_session")
        return response

    async def static(request):
        # 공개 파일만 명시적으로 제공해 서버 코드나 환경 파일이 노출되지 않게 합니다.
        name = request.match_info.get("name", "index.html")
        if name not in {"index.html", "app.js", "commands.js", "auth.js", "style.css", "sherbet.png"}:
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / name)

    app = web.Application(middlewares=[safety], client_max_size=4096)
    app.add_routes([web.get("/api/session", status), web.get("/auth/login", login),
                    web.get("/auth/callback", callback), web.get("/api/guilds", guilds),
                    web.get("/api/guilds/{guild_id}", guild_detail),
                    web.post("/auth/logout", logout), web.get("/", static), web.get("/{name}", static)])
    return app


if __name__ == "__main__":
    # .env를 읽지 않습니다. 실행 환경 또는 사용자 직접 입력으로만 받습니다.
    import argparse
    from getpass import getpass
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true")
    args = parser.parse_args()
    cid, secret = os.environ.get("DISCORD_CLIENT_ID", ""), os.environ.get("DISCORD_CLIENT_SECRET", "")
    if args.configure:
        cid = input("샤벳 Application ID: ").strip()
        secret = getpass("OAuth2 Client Secret (화면에 표시되지 않음): ")
    # 콜백 URL에는 인증 코드가 포함되므로 접근 로그를 남기지 않습니다.
    web.run_app(create_app(cid, secret), host="127.0.0.1", port=8766, access_log=None)
