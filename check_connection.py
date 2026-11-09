# -*- coding: utf-8 -*-
"""
Telegram connectivity diagnostic - run this FIRST when something fails:

    python check_connection.py

It checks, in order:
  1. Raw TCP access to a Telegram data center
     -> does your VPN actually cover Python's traffic?
  2. If not covered: automatic search for a working local proxy
     (v2rayN / NekoBox / Hiddify / Clash / hotspot gateway) and it
     prints the exact commands you need to run.
  3. Full Telethon connection (MTProto handshake with your API keys)
  4. Login status of your local session file

It never asks for your phone number and never sends anything
to Telegram - it is 100% read-only and safe to run anytime.
"""

import asyncio
import sys

import config

# Telegram DC2 (Amsterdam) - one of the IPs telethon itself uses
DC_IP = "149.154.167.51"
DC_PORT = 443

LINE = "-" * 62

# Common local proxy ports: v2rayN=10808, NekoBox/Nekoray=2080,
# Hiddify=12334, Clash=7897, generic socks=1080, v2rayN http=10809
LOCAL_PROXY_PORTS = [10808, 2080, 12334, 7897, 1080, 10809, 8080]

# If the VPN comes from a shared phone/hotspot, the proxy may live on
# the gateway: Android hotspot=192.168.43.1, Windows hotspot=192.168.137.1
GATEWAY_IPS = ["192.168.43.1", "192.168.137.1"]
GATEWAY_PORTS = [8080, 8081, 1080, 3128, 10808, 2080]


async def tcp_open(host, port, timeout=1.5):
    """Quick check: is this TCP port even open?"""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout)
    except Exception:
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return True


async def proxy_reaches_telegram(ptype, host, port, timeout=8):
    """Real end-to-end test: reach the Telegram DC through this proxy."""
    try:
        from python_socks.async_.asyncio import Proxy
        proxy = Proxy.from_url("%s://%s:%s" % (ptype, host, port))
        sock = await asyncio.wait_for(
            proxy.connect(dest_host=DC_IP, dest_port=DC_PORT),
            timeout=timeout)
    except Exception:
        return False
    try:
        sock.close()
    except Exception:
        pass
    return True


async def find_working_proxy(hosts=None, ports=None):
    """Scan hosts x ports, try socks5 then http, return first that
    can actually reach Telegram through itself, else None."""
    hosts = hosts if hosts is not None else ["127.0.0.1"]
    ports = ports if ports is not None else LOCAL_PROXY_PORTS
    for host in hosts:
        for port in ports:
            if not await tcp_open(host, port):
                continue
            print("    testing %s:%s ..." % (host, port))
            for ptype in ("socks5", "http"):
                if await proxy_reaches_telegram(ptype, host, port):
                    return ptype, host, port
    return None


def print_found_proxy(ptype, host, port):
    print()
    print("  >>> FOUND A WORKING PROXY:  %s://%s:%s" % (ptype, host, port))
    print("      (tested: it CAN reach Telegram right now)")
    print()
    print("  Run these commands in the SAME cmd window:")
    print()
    print("         set PROXY_TYPE=%s" % ptype)
    print("         set PROXY_HOST=%s" % host)
    print("         set PROXY_PORT=%s" % port)
    print("         python check_connection.py")
    print("         python main.py")
    if host != "127.0.0.1":
        print()
        print("  NOTE: %s is your gateway (the device sharing the VPN)." % host)
        print("        Keep its VPN + proxy app running at all times.")


def print_manual_fixes():
    print("  FIX - pick ONE:")
    print("   a) If the VPN comes from another device via 'VPN share'")
    print("      (hotspot / tethering), the share must be FULL-tunnel.")
    print("      Quick test: open https://web.telegram.org in your")
    print("      laptop's browser - if it does NOT load, the share")
    print("      is not a full tunnel.")
    print("   b) BETTER: install the VPN app on THIS laptop directly")
    print("      (v2rayN / Hiddify / NekoBox / Clash) and enable")
    print("      TUN mode. Then no proxy settings are needed.")
    print("   c) If a proxy app IS running on this laptop, start it and")
    print("      re-run this script - it will find the port for you.")
    print("   d) For a shared phone: install 'Every Proxy' on the phone")
    print("      and keep its VPN on, then re-run this script.")


async def step1_direct():
    """Can Python open a plain TCP connection to Telegram at all?"""
    print(LINE)
    print("STEP 1 - Raw TCP test to Telegram DC (%s:%s)" % (DC_IP, DC_PORT))
    print("         (no proxy - testing the direct route)")
    try:
        connect = asyncio.open_connection(DC_IP, DC_PORT)
        _reader, writer = await asyncio.wait_for(connect, timeout=15)
    except Exception as e:
        print("  FAIL - Python cannot reach Telegram servers directly.")
        print("         %s: %s" % (type(e).__name__, e))
        print()
        print("  MEANING: your VPN does NOT cover Python's traffic.")
        print("           (browser works, but Python traffic leaks out)")
        print()
        print("  Searching for a working proxy automatically ...")
        print("  (please wait, this can take up to ~30 seconds)")
        found = await find_working_proxy()
        if not found:
            print("    nothing on this laptop - checking the hotspot")
            print("    gateway (the device sharing the VPN) ...")
            found = await find_working_proxy(hosts=GATEWAY_IPS,
                                             ports=GATEWAY_PORTS)
        if found:
            print_found_proxy(*found)
        else:
            print()
            print("  No working proxy was found automatically.")
            print_manual_fixes()
        return False
    else:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        print("  OK - Telegram servers are reachable from Python.")
        print("       Your VPN routes Python traffic correctly.")
        return True


async def step1_through_proxy(ptype, host, port):
    """PROXY_* is configured - test the direct route through IT."""
    print(LINE)
    print("STEP 1 - TCP test to Telegram DC THROUGH your proxy")
    print("         %s://%s:%s" % (ptype, host, port))
    if await proxy_reaches_telegram(ptype, host, port, timeout=15):
        print("  OK - the configured proxy reaches Telegram.")
        return True
    print("  FAIL - this proxy does not reach Telegram right now.")
    print("         Checklist:")
    print("         - Is the VPN app actually running and connected?")
    print("         - Is PROXY_PORT the app's real local port?")
    print("  Searching for the right port automatically ...")
    found = await find_working_proxy()
    if found:
        print_found_proxy(*found)
    else:
        print_manual_fixes()
    return False


async def step2_telethon():
    """Can telethon complete the MTProto handshake with our API keys?"""
    print(LINE)
    print("STEP 2 - Telethon connection test (MTProto handshake)")
    from telethon import TelegramClient

    try:
        kwargs = config.client_kwargs()
    except ValueError as e:
        print("  FAIL - your proxy settings are invalid: %s" % e)
        print("         Check section 6 of config.py (or PROXY_* env vars).")
        return False, None

    if kwargs.get("connection") is not None:
        print("  Using MTProto proxy: %s" % (kwargs.get("proxy"),))
    elif kwargs.get("proxy"):
        print("  Using proxy: %s" % (kwargs.get("proxy"),))
    else:
        print("  No proxy configured - connecting directly.")

    try:
        client = TelegramClient(":memory:", config.API_ID, config.API_HASH, **kwargs)
        await asyncio.wait_for(client.connect(), timeout=30)
    except ValueError as e:
        print("  FAIL - bad proxy value: %s" % e)
        return False, None
    except Exception as e:
        print("  FAIL - telethon could not connect to Telegram.")
        print("         %s: %s" % (type(e).__name__, e))
        print()
        print("  If STEP 1 was OK but this fails, try:")
        print("   - a different proxy server / VPN server")
        print("   - update telethon:  pip install -U \"telethon<2\"")
        return False, None

    print("  OK - telethon connected to Telegram successfully.")
    print("       API keys are valid and the network path works.")
    return True, client


async def step3_session_status(client):
    """Is the local session file already logged in?"""
    print(LINE)
    print("STEP 3 - Login status of your local session")
    try:
        authorized = await client.is_user_authorized()
    finally:
        await client.disconnect()

    if authorized:
        print("  OK - you are already logged in.")
        print("       Just run:  python main.py")
    else:
        print("  NOTE - not logged in yet (normal for the first run).")
        print("         Run this to log in:  python main.py")
    return True


async def main():
    print("=" * 62)
    print("  TELEGRAM REPOSTER - CONNECTIVITY CHECK")
    print("=" * 62)

    # Which route should STEP 1 test?
    try:
        kwargs = config.client_kwargs()
        proxy = kwargs.get("proxy")
    except ValueError as e:
        print(LINE)
        print("STEP 1 - proxy settings problem: %s" % e)
        print()
        print("  Fix config.py section 6 or clear the env variables:")
        print("         set PROXY_TYPE=")
        print("         set PROXY_HOST=")
        print("         set PROXY_PORT=")
        return 1

    if kwargs.get("connection") is not None:
        print(LINE)
        print("STEP 1 - skipped (MTProto proxy is tested in STEP 2).")
        ok = True
    elif proxy:
        ok = await step1_through_proxy(proxy[0], proxy[1], proxy[2])
    else:
        ok = await step1_direct()
    if not ok:
        print(LINE)
        print("RESULT: BLOCKED - fix the VPN/proxy first, then re-run.")
        print("Run again:  python check_connection.py")
        return 1

    ok, client = await step2_telethon()
    if not ok:
        print(LINE)
        print("RESULT: FAILED at step 2 - see the hints above.")
        return 1

    await step3_session_status(client)
    print(LINE)
    print("RESULT: ALL GOOD - you can run the bot:")
    print("           python main.py")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nCheck aborted.")
        sys.exit(1)
