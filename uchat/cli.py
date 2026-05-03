from __future__ import annotations

import sys
import threading
import time
from queue import Empty, Queue

from uchat.adapters import BilibiliLiveInputAdapter, ConsoleInputAdapter, ConsoleOutputAdapter, ConsoleTTSAdapter, OBSOutputAdapter, ServiceTTSAdapter
from uchat.config import ConfigError, Settings, load_dotenv
from uchat.debug import DebugWriter
from uchat.identity import IdentityService, InMemoryIdentityStore, SQLiteIdentityStore
from uchat.logging_utils import configure_logging, get_logger
from uchat.ltmem import LTMemGateway
from uchat.memory import MemoryOrchestrator
from uchat.models import ModelConfigError, ModelRouter, ModelsConfig
from uchat.prompts import PromptManager
from uchat.runtime import RuntimeOrchestrator


def main() -> None:
    load_dotenv()
    try:
        settings = Settings.load()
        models_config = ModelsConfig.load()
    except (ConfigError, ModelConfigError) as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    configure_logging(settings.logging)
    logger = get_logger("uchat.cli")
    logger.info("UChat console runtime starting", extra={"stage": "startup", "service": "runtime", "status": "ok"})

    ltmem_gateway = LTMemGateway(settings.ltmem)
    memory = MemoryOrchestrator(settings.ltmem, ltmem_gateway)
    health = memory.startup_healthcheck()
    memory.start()

    if health.disabled:
        logger.info(
            "LTMem disabled by config",
            extra={"stage": "ltmem_health", "service": "ltmem", "status": "disabled", "degraded": False},
        )
    elif health.checked and health.available:
        logger.info(
            f"LTMem health check ok: {health.response}",
            extra={"stage": "ltmem_health", "service": "ltmem", "status": "ok", "degraded": False},
        )
    elif health.checked and not health.available:
        log_method = logger.error if health.required else logger.warning
        status = "error" if health.required else "degraded"
        log_method(
            f"LTMem health check failed: {health.error}",
            extra={"stage": "ltmem_health", "service": "ltmem", "status": status, "degraded": not health.required},
        )
        if health.required:
            print(f"LTMem 不可用：{health.error}", file=sys.stderr)
            print("请先确认 E:\\PycharmProjects\\LTMem 服务已启动且 /health 返回 ok，再启动 UChat。", file=sys.stderr)
            memory.close()
            raise SystemExit(3)

    model_router = ModelRouter(models_config)
    output_adapter = ConsoleOutputAdapter(settings.debug)
    shared_console = getattr(output_adapter, "console", None)
    tts_adapter = _build_tts_adapter(settings, console=shared_console)
    subtitle_adapter = _build_subtitle_adapter(settings)
    identity_service = _build_identity_service(settings)
    runtime = RuntimeOrchestrator(
        settings=settings,
        memory=memory,
        model_router=model_router,
        prompts=PromptManager(root_dir=settings.prompt.root_dir, locale=settings.runtime.locale, version=settings.prompt.version),
        debug_writer=DebugWriter(settings.debug),
        observer=output_adapter,
        output_adapter=output_adapter,
        tts_adapter=tts_adapter,
        subtitle_adapter=subtitle_adapter,
        identity_service=identity_service,
    )
    input_adapter = _build_console_input_adapter(settings)
    live_adapter = _build_live_input_adapter(settings)

    print("UChat 控制台已启动。输入 /quit 退出。")
    if live_adapter is not None:
        print("直播输入已启用，将通过 bilibili_gateway 轮询结构化事件。")
    try:
        if live_adapter is not None:
            _run_live_loop(runtime=runtime, input_adapter=input_adapter, live_adapter=live_adapter, logger=logger)
        else:
            _run_console_loop(runtime=runtime, input_adapter=input_adapter, logger=logger)
    finally:
        if live_adapter is not None:
            live_adapter.stop()
        identity_service.close()
        memory.close()
        model_router.close()
        close_method = getattr(tts_adapter, "close", None)
        if callable(close_method):
            close_method()
        close_method = getattr(subtitle_adapter, "close", None)
        if callable(close_method):
            close_method()


def _build_console_input_adapter(settings: Settings):
    try:
        return ConsoleInputAdapter(
            scene_id=settings.runtime.scene_id,
            session_window_id=settings.runtime.session_window_id,
        )
    except TypeError:
        return ConsoleInputAdapter()


def _build_live_input_adapter(settings: Settings) -> BilibiliLiveInputAdapter | None:
    if settings.runtime.scene_kind != "live_stream":
        return None
    bilibili_service = settings.services.platform.get("bilibili")
    if bilibili_service is None or not bilibili_service.url:
        return None
    return BilibiliLiveInputAdapter(
        scene_id=settings.runtime.scene_id,
        session_window_id=settings.runtime.session_window_id,
        room_id="",
        base_url=bilibili_service.url,
        reconnect_backoff_ms=500,
        preferred_connection_mode="live",
    )


def _build_tts_adapter(settings: Settings, *, console=None):
    service = settings.services.tts
    if service.url:
        return ServiceTTSAdapter(base_url=service.url)
    return ConsoleTTSAdapter(settings.debug, console=console)


def _build_subtitle_adapter(settings: Settings) -> OBSOutputAdapter | None:
    service = settings.services.obs
    if service.url:
        return OBSOutputAdapter(base_url=service.url)
    return None


def _build_identity_service(settings: Settings) -> IdentityService:
    if settings.identity.store_type == "memory":
        store = InMemoryIdentityStore()
    else:
        store = SQLiteIdentityStore(settings.identity.sqlite_path)
    return IdentityService(
        store=store,
        default_console_person_id=settings.identity.default_console_person_id,
    )


def _run_console_loop(*, runtime: RuntimeOrchestrator, input_adapter, logger) -> None:
    while True:
        try:
            payload = input_adapter.read()
            if isinstance(payload, str):
                runtime.process_console_text(payload)
                continue
            if payload.content_norm.lower() in {"/quit", "/exit"}:
                raise KeyboardInterrupt
            runtime.process_event(payload)
        except KeyboardInterrupt:
            print("已退出。")
            break
        except Exception:
            logger.exception("console turn failed", extra={"stage": "turn", "service": "runtime", "status": "error"})
            print("本轮处理失败，详情见后台日志。", file=sys.stderr)


def _run_live_loop(*, runtime: RuntimeOrchestrator, input_adapter, live_adapter: BilibiliLiveInputAdapter, logger) -> None:
    command_queue: Queue[str] = Queue()
    stop_event = threading.Event()
    live_connected = False
    try:
        live_adapter.start()
        live_connected = True
    except Exception:
        logger.exception("live adapter connect failed", extra={"stage": "live_connect", "service": "runtime", "status": "error"})
        print("直播输入连接失败，当前将继续保留控制台模式运行；详情见后台日志。", file=sys.stderr)

    def _console_reader() -> None:
        while not stop_event.is_set():
            try:
                text = input_adapter.read_text()
            except (EOFError, KeyboardInterrupt):
                command_queue.put("/quit")
                return
            command_queue.put(text)

    reader = threading.Thread(target=_console_reader, name="uchat-console-reader", daemon=True)
    reader.start()

    try:
        while True:
            try:
                while True:
                    command = command_queue.get_nowait()
                    runtime.process_console_text(command)
            except Empty:
                pass
            except KeyboardInterrupt:
                print("已退出。")
                break
            except Exception:
                logger.exception("console command failed", extra={"stage": "turn", "service": "runtime", "status": "error"})
                print("本轮控制台命令处理失败，详情见后台日志。", file=sys.stderr)

            if live_connected:
                try:
                    _process_live_gateway_events(runtime=runtime, live_adapter=live_adapter)
                except Exception:
                    logger.exception("live poll failed", extra={"stage": "live_poll", "service": "runtime", "status": "error"})
                    print("直播事件拉取失败，详情见后台日志。", file=sys.stderr)
                    time.sleep(1.0)
            time.sleep(0.25)
    finally:
        stop_event.set()


def _process_live_gateway_events(*, runtime: RuntimeOrchestrator, live_adapter: BilibiliLiveInputAdapter) -> None:
    events = live_adapter.poll_events(limit=20)
    for event in events:
        runtime.process_event(event)
