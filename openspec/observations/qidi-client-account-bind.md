# QIDI client account binding and login QR

How QIDI's touchscreen app (`qidi-client.service`, `/home/qidi/QIDI_Client/bin/qidiclient`) obtains the code behind the Account page's login QR code, and how it learns that the phone finished binding.

## Evidence boundary

- **Static-recovered:** Max 4 `qidiclient` SHA-256 `b5285d1a52f1180757360d0893f0f8ed619aaaa31d2d621917c0a963bc3fde4e` (QIDI's firmware `01.01.07.01` build; the test printer ran `b0e9a4a6…`, the same build with Valve Oil's patches, which changes no function analysed here), aarch64, stripped but carrying lambda type names and source paths (`/builds/qidi/3dprinter_m4/qidiclient/ui/draw_ui/QD_MAX4/5.0_inch/draw_page_account.cpp`). Findings come from disassembly of the request, header, signature, and account-page functions; they establish code shape, not that every branch runs.
- **Runtime-confirmed:** the signing string, request URL, report body, and server responses were read from the client itself on 2026-11-12: a second `qidiclient` process was run as `qidi` with an `LD_PRELOAD` shim that redirected `/root/…` log paths to a writable directory and logged `memcpy` buffers containing the signing prefix. The always-on root process was not restarted and its own journal log was not readable.
- **Runtime-confirmed:** the endpoints were replayed from both the printer and another host with the client's exact body, headers, and signature.
- The Plus 5 client (`6c09a58a…`, `01.01.02.01`) was not disassembled; its log and replayed requests are runtime evidence. Only a logged-out printer was observed; the post-bind account state, callbacks, and MQTT handover are static-recovered.
- Hardware identifiers are placeholders: `<SERIAL>` is the 16-character lowercase `Serial` value from `/proc/cpuinfo`, `<SERIAL_UPPER>` its uppercase form, `<MAC>` the eth0 MAC, `<LAN_IP>` the printer's address. Region names, error codes, and message text are literal.

## Flow

1. The client requests a bind code from the cloud: once shortly after startup on a logged-out printer, and again from the Account page's refresh control.
2. The cloud returns a one-time code; the client renders it inside a URL as the QR content.
3. The phone scans it, the user confirms in the QIDI Maker app.
4. The printer observes the binding through a status channel and through its periodic device report, then takes account credentials from the response.

The client does not encode a bare code: the QR is a link to `https://www.qidimaker.com/en/device/education/qr-code` with query parameters.

## Device identity

- **Runtime-confirmed:** the serial comes from `cat /proc/cpuinfo | grep Serial| awk '{printf "%s", substr($0, index($0, ":") + 2)}'`, the same shell-out used by the firmware check. The client uppercases it for the request body (the captured signing string contained `"serialNumber":"<SERIAL_UPPER>"`).
- **Static-recovered:** `X-DeviceId` and the QR's `sn` parameter are filled from the same device-info member; the observed request buffers carried the uppercase serial as the only 16-character device value.
- **Runtime-confirmed:** the default device name is `3DP-` followed by the first and last three characters of the serial (the Account page showed `3DP-<first3>-<last3>`). It is user-editable and is sent as the QR's `deviceName`.
- **Static-recovered:** the Region setting supplies the QR's `region` parameter and selects the API/download host entry. In this binary the `X-Region` header is written from the two-byte literal `NA`, so the header does not follow the setting. The map's default API base is `https://api.qidimaker.com`, with a CN entry for `https://api.qidimaker.com.cn`; the remaining entries select `download.{apac,eea,na,others}.qidi3dprinter.com`. Which map key is read at runtime is unresolved.

## Bind code request

**Runtime-confirmed** (captured URL and body, replayed responses):

```
POST https://api.qidimaker.com/auth/v1/device/get/bindCode
{"serialNumber":"<SERIAL_UPPER>"}
```

The body is compact JSON with no spaces. Headers, all required by the endpoint:

| Header | Value |
| --- | --- |
| `Content-Type` | `application/json` |
| `Accept-Language` | `en_US` or `zh_CN`; selects the QR host/language pair |
| `X-Platform` | `3DPrinter` |
| `X-DeviceType` | `QD_MAX4` |
| `X-Timezone` | offset built at request time |
| `X-Version` | client version string, `01.01.07.01` in this build |
| `X-Region` | `NA` |
| `X-Timestamp` | epoch milliseconds |
| `X-Nonce` | random lowercase hex, 32 characters |
| `X-DeviceId` | `<SERIAL_UPPER>` |
| `X-Signature` | see below |

Header names and the `3DPrinter`, `QD_MAX4`, `NA`, and version values are the header builder's literals (**static-recovered**); `X-Timezone`, `X-Timestamp`, and `X-Nonce` are computed per request. The accepted replay used `X-Timezone: +00:00`.

**Runtime-confirmed** signing rule: `X-Signature` is the lowercase hex HMAC-SHA256 of the canonical string `qidi3d ` + the body with all whitespace removed, keyed with `qid3d-from-2025-CAFE-BABE`. The captured canonical string for a bind-code call was `qidi3d {"serialNumber":"<SERIAL_UPPER>"}`. The body alone is signed; timestamp, nonce, and device ID are not part of it. The [firmware-check capture](https://github.com/thelegendtubaguy/QidiMax4CommunityWiki/blob/main/docs/qidi_client_firmware_update_flow.md) records the same rule and key for a `qidi3d null` payload; that endpoint does not appear to verify the value.

**Runtime-confirmed:** the server keeps nonces at least long enough to reject reuse with code `2014`, and checks that before the signature, so a failed attempt still consumes its nonce.

Response: `{"code":200,"message":"Success","data":"<32 hex chars>"}`. `data` is the bind code shown in the QR.

## QR contents

**Static-recovered**, built locally once the code arrives (no second request):

```
<host>/device/education/qr-code?model=QD_MAX4&region=<region>&sn=<sn>&ver=<X-Version>&t=<bind code>&deviceName=<device name>
```

- `<host>` is `https://www.qidimaker.com/en` or `https://www.qidimaker.com.cn/zh`, chosen by testing the request's language string for `CN`.
- `t` is the event payload of the bind-code success, i.e. the code from `data`.
- Rendering uses LVGL's `qrcodegen`. Before any code arrives the QR object holds the literal `Hello world`, and `qr_loading_mask.bin` covers it; the Account page's refresh icon re-runs the request.

## After the scan

- **Static-recovered:** the client subscribes to `/auth/v1/get/bind/status` for the QR state. `resource/model_config/QD_MAX4.json` sets `support_no_sse: true`, so the Max 4 path is not the SSE one; the Plus 5 model config omits that flag.
- **Static-recovered:** account-event names drive the page: `obtain_device_code_success`, `obtain_device_code_failed`, `sse_qr_invalid`, `sse_qr_used`, `sse_bind_success`, `bind_success`, `clear_user_info`, `http_request_error`, `region_updated`.
- **Runtime-confirmed**, report body sent periodically while unbound, signed with the same rule:

```json
{"activeDo":false,"bind":false,"firmwareVersion":"01.01.07.01","ipAddress":"<LAN_IP>","macAddress":"<MAC>","region":"NA","serialNumber":"<SERIAL_UPPER>"}
```

  A failed report logs `[HTTP] Func: [设备报告请求错误, 绑定状态: 未绑定]`.
- **Static-recovered:** `/auth/v1/device/bind/callback` posts `serialNumber`, `macAddress`, `ipAddress`, `firmwareVersion`, and `directMqtt`, and is the only request path paired with `Authorization: Bearer `; the token is the account `accessToken`, which also reaches Moonraker as the MQTT credential source. A `40004` response is logged as `Device report: binding not found (40004), triggering unbind`.

## Page states

**Static-recovered:** the account page is drawn with a placeholder QR, a loading mask, and the "Scan the QR code to log in" text, then updated by an integer state set from the event names above. The refresh icon re-runs the bind-code request, and every draw of the page resets the QR object to the placeholder. Observed transitions: bind success → 5, device code success → 1 (QR set), device code failure → 2, QR invalid → 3, QR used → 4, clear user info → 6, HTTP error → 8. State 7's trigger is unresolved. On the observed printer the page stayed in that state while the bind-code request had already failed: placeholder QR under the mask, spinner animating, no outbound connection open over five minutes of sampling.

## Server error codes

Codes returned by `api.qidimaker.com`, from replaying the bind-code endpoint unless noted:

| Code | Meaning |
| --- | --- |
| `2004` | `Accept-Language` missing |
| `2005` | `X-Timezone` missing |
| `2006` | `X-Platform` missing |
| `2007` | `X-DeviceType` missing |
| `2008` | `X-Version` missing |
| `2009` | `X-Signature` missing |
| `2010` | `X-Timestamp` missing |
| `2013` | invalid `X-Signature`; also returned when `X-Nonce` or `X-DeviceId` is absent |
| `2014` | duplicate `X-Nonce` |
| `2017` | `X-Region` missing |
| `2018` | `X-Region` not accepted (`OTHERS`, arbitrary values) |
| `40093` | `Please upgrade to version 01.01.01.01 to support APP scan-to-connect` |
| `40004` | binding not found (device report; client initiates unbind) |

## Version gate

**Runtime-confirmed (2026-11-12):** the backend validates the signature differently depending on `X-Version`, and the switch is exactly at the version this client reports. Identical body, headers, and signing rule:

| `X-Version` | Result |
| --- | --- |
| `01.01.01.01` … `01.01.07.00` (sampled at `01.01.01.01`, `01.01.01.02`, `01.01.02.00`, `01.01.03.00`, `01.01.04.00`, `01.01.05.00`, `01.01.06.00`, `01.01.06.05`, `01.01.07.00`) | `200` with a bind code |
| `01.01.07.01`, `01.02.00.00`, `02.00.00.00` | `401` `2013 Invalid X-Signature` |
| `01.01.00.99`, `01.01.01.00` | `400` `40093` (too old for scan-to-connect) |

The current client sends `01.01.07.01` and signs with the rule above, so every signed request it makes is rejected. Its own log on the test printer shows the consequence:

```
[HTTP] Func: [获取设备绑定码失败] | statusCode: 401 | errorBody: {"code":2013,"message":"Invalid X-Signature in request header"}
[HTTP] Func: [设备报告请求错误, 绑定状态: 未绑定] | statusCode: 401 | errorBody: {"code":2013,...}
```

The same switch applies to the device report: with `01.01.07.00` the signature passes and the call fails later on a missing field (`must not be null`), with `01.01.07.01` it returns `2013`.

The Account page therefore keeps the `Hello world` placeholder QR under its loading mask, and the printer never binds. Endpoints that do not verify the signature, such as the firmware check, keep working, which is why the printer still reports and updates.

HMAC-SHA256 over the same key with the timestamp, nonce, and device ID added to the canonical string in any tested order and separator (1,700+ permutations) was still rejected at `01.01.07.01`, as were SHA-256 concatenation, key, algorithm, and encoding variants. The Plus 5 `01.01.02.01` client failed the same way (`2013` on the bind code and device report) through 2026-11-12 23:16 UTC.

**Runtime-confirmed (2026-11-13, about 03:35 UTC):** QIDI changed the gate server-side, and both stock clients bind again. At 03:34 the Max 4's bind-code request at `01.01.07.01` returned `500 Internal Server Error`; from 03:37 it returned a code. The gate is now per model: a request passes up to the model's current release and fails above it with `2013`, whichever printer's serial it carries:

| `X-DeviceType` | Accepted | `2013` |
| --- | --- | --- |
| `QD_MAX4` | `01.01.01.01` … `01.01.07.01` | `01.01.08.00` |
| `QD_PLUS5` | `01.01.01.01` … `01.01.02.01` | `01.01.03.00` and up |

With the unmodified client logic restored, the Max 4 obtained a bind code, had its device report accepted, and opened the bind-status channel; the Plus 5 showed a login QR code without any change on the printer. A client whose firmware version is newer than the version QIDI's server knows for its model would hit this gate again.

## Reproduction

```bash
SN=<SERIAL_UPPER>
SIG=$(printf '%s' "qidi3d {\"serialNumber\":\"$SN\"}" |
  openssl dgst -sha256 -hmac 'qid3d-from-2025-CAFE-BABE' -binary |
  od -An -tx1 | tr -d ' \n')
curl -sS -X POST 'https://api.qidimaker.com/auth/v1/device/get/bindCode' \
  -H 'User-Agent: curl/7.74.0' -H 'Content-Type: application/json' \
  -H 'Accept-Language: en_US' -H 'X-Platform: 3DPrinter' -H 'X-DeviceType: QD_MAX4' \
  -H "X-Timezone: +00:00" -H 'X-Version: 01.01.07.00' -H 'X-Region: NA' \
  -H "X-Timestamp: $(($(date +%s%N)/1000000))" \
  -H "X-Nonce: $(openssl rand -hex 16)" -H "X-DeviceId: $SN" -H "X-Signature: $SIG" \
  --data-raw "{\"serialNumber\":\"$SN\"}"
```

Since 2026-11-13 `X-Version` up to `01.01.07.01` returns a code for `QD_MAX4`, and anything above it returns `2013`. Each attempt consumes its nonce.

## Header version consumers

**Static-recovered** (Max 4 `b5285d1a…`, `01.01.07.01`): the header builder (`0x64d770`) takes the body, the signing key, and a version string; the version only becomes `X-Version`, and the HMAC (`0x690720`) covers the key and body. The literal `01.01.07.01` at `0x9bba60` is loaded at 30 sites. Ten build the builder's version argument (bind code, bind status, bind callback, device report, FRP, file upload and its signed follow-up, AI video callback, log upload callback, released-firmware list); the firmware check (`0x654718`) loads it once for both `currentVersion` and `X-Version`. The rest feed the QR `ver` parameter, firmware-version comparisons, the log header, the Moonraker identity, and the firmware page.

## Unresolved

- The canonical string the backend expects above a model's current release, and whether a new QIDI release will sign that way.
- The `sn` and `deviceName` member sources are static only; no request carried the rendered QR URL, because the code never arrived.
- Which map key selects the API host, and how the Region setting interacts with the hardcoded `NA` header.
- The status channel on Max 4 (`support_no_sse: true`): polling cadence, and what clears the loading mask on success.
