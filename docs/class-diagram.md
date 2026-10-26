# 类图 - Han记牌器

> 当前实现采用 Command/Response 回执协议；启动入口为 `core/controller_process.py`，设置协调器为 `ui/settings_controller.py`。完整交互见 [顺序图](sequence-diagram.md)。

> 展示项目中核心类的属性、方法及关系

```mermaid
classDiagram
    direction TB

    %% ========== UI 层 ==========
    class CardUI {
        -layout_name: str
        -detect_interval_sec: float
        -played_cards: dict
        -played_cards_labels: dict[str, QLabel]
        -card_order: list[str]
        -worker: InferenceWorker
        -timer: QTimer
        -_busy: bool
        -_is_settings_open: bool
        -_show_played_cards: bool
        -name_labels: dict[str, QLabel]
        -count_labels: dict[str, QLabel]
        -_last_count_values: dict[str, int]
        -_last_depleted_values: dict[str, bool]
        -_last_played_signature: tuple | None
        -_last_cycle_start: float
        -_last_round_ms: float
        +request_one_update()
        +on_result_ready(remain_cards, zone_cards, inference_ms)
        +on_worker_error(err_text)
        +on_worker_finished()
        +on_settings_clicked()
        +on_pause_clicked()
        +on_reset_clicked()
        +on_layout_changed(index)
        +on_device_changed(index)
        +on_interval_changed(index)
        +on_reset_time_changed(index)
        +on_frame_length_changed(index)
        +on_always_on_top_changed(index)
        +on_show_played_cards_changed(index)
        +on_debug_mode_changed(index)
        +on_save_debug_images_changed(index)
        +on_show_timing_changed(index)
        +on_model_changed(index)
        +on_confidence_changed(index)
        +on_game_changed(index)
        +on_layout_editor_clicked(settings_dialog)
        +on_layout_delete_clicked(settings_dialog, layout_name)
        -_update_played_cards_visibility()
        -_apply_always_on_top_state(always_on_top)
        -_reapply_topmost_if_enabled(settings_dialog)
        -_switch_layout_by_name(layout_name)
        -_touch_no_target_time()
        -_start_timer_if_allowed()
        -_stop_timer_if_exists()
        -_ensure_widgets_attached()
        -_reset_ui_to_total()
        +closeEvent(event)
    }

    class SettingsDialog {
        -tab_widget: QTabWidget
        -combo_game: QComboBox
        -combo_layout: QComboBox
        -combo_device: QComboBox
        -combo_interval: QComboBox
        -combo_reset_time: QComboBox
        -combo_frame_length: QComboBox
        -combo_always_on_top: QComboBox
        -combo_show_played_cards: QComboBox
        -combo_debug_mode: QComboBox
        -combo_save_debug_images: QComboBox
        -combo_show_timing: QComboBox
        -combo_model: QComboBox
        -combo_confidence: QComboBox
        -on_reset_callback: callable
        -on_interval_change_callback: callable
        -on_layout_change_callback: callable
        -on_device_change_callback: callable
        -on_reset_time_change_callback: callable
        -on_frame_length_change_callback: callable
        -on_always_on_top_change_callback: callable
        -on_show_played_cards_change_callback: callable
        -on_debug_mode_change_callback: callable
        -on_save_debug_images_change_callback: callable
        -on_show_timing_change_callback: callable
        -on_layout_editor_callback: callable
        -on_layout_delete_callback: callable
        -on_model_change_callback: callable
        -on_confidence_change_callback: callable
        -on_game_change_callback: callable
        +set_current_interval(interval_text)
        +set_current_layout(layout_name)
        +set_current_device(device_choice)
        +set_current_game(game_name)
        +set_current_reset_time(reset_time)
        +set_current_frame_length(frame_length)
        +set_current_always_on_top(always_on_top)
        +set_current_show_played_cards(show_played_cards)
        +set_current_debug_mode(debug_mode)
        +set_current_save_debug_images(save_debug_images)
        +set_current_show_timing(show_timing)
        +set_current_model(model_name)
        +set_current_confidence(confidence)
        +refresh_layout_list(selected_name)
        +refresh_model_list(selected_name)
    }

    class LayoutEditorDialog {
        -canvas: RectCanvas
        -edit_layout_name: QLineEdit
        -combo_window_title: QComboBox
        -combo_region: QComboBox
        -chk_set_current: QCheckBox
        -_captured_image: PIL.Image | None
        -_lowered_windows: list
        -_window_restore_states: list
        -_windows_pushed_back: bool
        -_on_restore_topmost: callable
        -_stay_on_top: bool
        +saved_layout_name: str | None
        +saved_set_current: bool
        -_reload_window_titles()
        -_capture_window()
        -_preview_layout()
        -_save_layout()
        -_send_app_windows_to_bottom()
        -_restore_app_windows_after_capture()
        -_adjust_dialog_size_for_image()
        -_fit_canvas_to_viewport()
        -_build_normalized_layout()
        -_sync_region_labels()
        +eventFilter()
        +closeEvent()
    }

    class RectCanvas {
        -_base_pixmap: QPixmap | None
        -_display_scale: float
        -_active_key: str
        -_rects: dict[str, tuple]
        -_drawing: bool
        -_start: QPoint
        -_current: QPoint
        +rect_changed: Signal
        +image_size: tuple
        +set_active_key(key)
        +set_pixmap_from_pil(pil_image)
        +fit_to_viewport(viewport_width, viewport_height)
        +clear_rects()
        +set_rects(rects)
        +get_rects()
        +mousePressEvent(event)
        +mouseMoveEvent(event)
        +mouseReleaseEvent(event)
    }

    class PreviewDialog {
        -_source_pixmap: QPixmap
        -_label: QLabel
        -_scroll: QScrollArea
        -_update_scaled_preview()
        +resizeEvent(event)
        +showEvent(event)
    }

    class LayoutItemDelegate {
        +paint(painter, option, index)
        +get_delete_button_rect(item_rect)$ QRect
    }

    class LayoutComboView {
        -_combo: QComboBox
        -_delete_handler: callable
        -_delegate: LayoutItemDelegate
        +mousePressEvent(event)
    }

    %% ========== 推理层 ==========
    class InferenceWorker {
        -_layout_name: str
        -_game_name: str
        -_cmd_queue: mp.Queue
        -_result_queue: mp.Queue
        -_process: mp.Process | None
        -_poll_timer: QTimer
        +result_ready: Signal
        +error: Signal
        +finished: Signal
        +state_changed: Signal
        +command_completed: Signal
        +state: str
        +restart()
        +can_detect() bool
        +start()
        +stop()
        +is_alive() bool
        +request_detect()
        +request_reset()
        +switch_layout(layout_name)
        +switch_model(model_name)
        +switch_device(device_name)
        +switch_game(game_name)
        +update_settings(updates)
        +touch_time()
        -_poll_result()
    }

    class SettingsController {
        +apply(save, request, after) bool
        +failed: Signal
        +applied: Signal
        -_pending: dict
    }

    class Command {
        +request_id: int
        +generation: int
        +name: str
        +payload: object
    }

    class Response {
        +status: str
        +request_id: int
        +generation: int
        +remain_cards: dict
        +zone_cards: dict
        +error: str
    }

    class GameController {
        -game_name: str
        -layout_name: str
        -layout_config: dict
        -screen_capture: ScreenCapture
        -yolo_inferencer: YoloInferencer
        -layout_analyzer: LayoutAnalyzer / AutoLayoutAnalyzer
        -tracker: BaseCardTracker
        -debug_manager: ImageSaver
        +detect() tuple
        +reset()
        +switch_layout(layout_name, layout_config)
        +switch_model(model_name)
        +switch_device(device_name)
        +switch_game(game_name)
        +update_settings(updates)
        +touch_time()
        -_format_zone_cards(show_cards) dict
    }

    %% ========== 核心层 ==========
    class BaseCardTracker {
        <<abstract>>
        +layout_name: str | None
        +state: int
        +frame_caches: dict[str, list]
        +show_cards: dict[str, list]
        +has_found_empty: dict[str, bool]
        +remain_cards: dict[str, int]
        +no_target_time: float
        +debug_manager: ImageSaver | None
        -_last_yolo_ms: float
        +reset()
        +get_cards_number(frame_data, yolo_ms) tuple
        +run_game(frame_data, yolo_ms)
        +translate_boxes_to_cards(region_boxes) dict
        #_presses_one_frame(frame_data, yolo_ms)
        #_check_card(lst) bool
        #_delete_played_cards(lst)
        #_process_all_played_zones()
        #_get_validity_region()* str
        +should_start_game()* bool
        +should_start_recording()* bool
        +on_game_started()*
        +on_start_recording()*
        +process_played_cards(zone_key, cards)*
    }

    class DoudizhuTracker {
        +_get_validity_region() str
        +should_start_game() bool
        +should_start_recording() bool
        +on_game_started()
        +on_start_recording()
        +process_played_cards(zone_key, cards)
    }

    class YoloInferencer {
        +yolo_iou: float
        +yolo_conf: float
        +weight_path: str
        +model: YOLO
        +device: str
        +debug_manager: ImageSaver | None
        +detect(img) tuple[object, float]
        -__load_model() tuple[YOLO, str]
        -__load_tensorrt(engine_path) YOLO | None
        -__load_onnx(onnx_path) YOLO | None
    }

    class LayoutAnalyzer {
        +layout_config: dict
        +parse_and_sort(raw_result, img_size) dict[str, list[dict]]
        +sort_cards_by_topright_rowwise(dets, max_rows) list[dict]
    }

    class ScreenCapture {
        +window_title: str | None
        +capture_window() PIL.Image | None
        -_release_gdi()$
    }

    class ImageSaver {
        +base_dir: str
        +raw_root: str
        +yolo_root: str
        +current_game_id: str | None
        +current_index: int
        +limit_reached_notified: bool
        +next_game_number: int
        +max_games: int
        +max_images_per_game: int
        +enabled: bool
        +bootstrap(debug_enabled)
        +bootstrap_static(base_dir, debug_enabled)$
        +set_enabled(enabled)
        +start_new_game() str | None
        +save_frame(raw_image, yolo_image) bool
        -_trim_old_games()
        -_find_next_game_number() int
        -_extract_game_number(game_id)$ int | None
        -_game_sort_key(game_name)$ tuple
    }

    %% ========== 工厂函数（非类） ==========
    %% run_controller_loop(cmd_queue, result_queue, layout_name, game_name, snapshot)
    %% create_tracker(game_name, layout_name, debug_manager) -> BaseCardTracker
    %% 以上两者为函数，非类，仅在关系线中标注

    %% ========== 配置层 ==========
    class settings {
        <<module>>
        +BASE_DIR: str
        +RESOURCE_DIR: str
        +CONFIG_PATH: str
        +GAMES_DIR: str
        +YOLO_MODEL_NAME: str
        +YOLO_MODEL_PATH: str
        +RESET_TIME: float
        +DETECT_INTERVAL_SEC: float
        +YOLO_CONFIDENCE_THRESHOLD: float
        +YOLO_IOU_THRESHOLD: float
        +YOLO_TO_CARD_MAPPING: dict[str, str]
        +WINDOW_LAYOUTS: dict
        +CURRENT_LAYOUT: str
        +FRAME_LENGTH: int
        +DEBUG_MODE: bool
        +SAVE_DEBUG_IMAGES: bool
        +SHOW_TIMING: bool
        +DEVICE_CHOICE: str
        +ALWAYS_ON_TOP: bool
        +SHOW_PLAYED_CARDS: bool
        +LITTLE_JOKER_SHOWN: str
        +BIG_JOKER_SHOWN: str
        +TOTAL_CARDS: dict[str, int]
        +GAME_NAME: str
        +GAME_DISPLAY_NAME: str
        +PLAYED_ZONES: list[dict]
        +LAYOUT_REGIONS: list[dict]
        +WAIT_BEGIN: int
        +HAS_STARTED: int
        +STARTED_RECORD_CARD: int
        +load_config() dict
        +_resolve_resource_dir() str
        +_scan_model_dirs() list[str]
        +_resolve_model_path(model_name) str
        +_scan_game_configs() list[str]
        +_load_game_config(game_name) dict
        +_ensure_runtime_config_exists()
        +save_device_choice(device_choice)
        +save_model_choice(model_name)
        +save_confidence_choice(confidence)
        +save_reset_time(reset_time)
        +save_frame_length(frame_length)
        +save_detect_interval(detect_interval)
        +save_always_on_top(always_on_top)
        +save_show_played_cards(show_played_cards)
        +save_debug_mode(debug_mode)
        +save_debug_images_choice(save_debug_images)
        +save_show_timing_choice(show_timing)
        +save_game_choice(game_name)
        +save_current_layout(layout_name)
        +save_window_layout(layout_name, window_title, layout_dict)
        +delete_window_layout(layout_name)
    }

    %% ========== 工具层 ==========
    class coord {
        <<module>>
        +REGION_KEYS: list[str]
        +normalize_rect(rect, width, height) tuple
        +denormalize_rect(norm_rect, width, height) tuple
        +sanitize_pixel_rect(rect, width, height) tuple
        +normalize_layout(pixel_layout, width, height) dict
    }

    class service {
        <<module>>
        +list_visible_window_titles() list[str]
        +capture_window_by_title(window_title) PIL.Image | None
        +get_layout_config(layout_name) tuple | None
        +save_layout(layout_name, window_title, normalized_layout, set_current)
    }

    class validator {
        <<module>>
        +validate_normalized_layout(layout) tuple[bool, str]
        +build_preview_image(base_image, normalized_layout) PIL.Image
    }

    class trans_yolo_names_to_string {
        <<module>>
        +tool_trans(lst) str
        +trans_yolo_names_to_string(lst) str
    }

    class styles {
        <<module>>
        +load_qss(app, qss_path)
    }

    %% ========== 关系 ==========
    CardUI --|> QMainWindow : 继承
    SettingsDialog --|> QDialog : 继承
    LayoutEditorDialog --|> QDialog : 继承
    PreviewDialog --|> QDialog : 继承
    RectCanvas --|> QLabel : 继承
    InferenceWorker --|> QObject : 继承
    LayoutItemDelegate --|> QStyledItemDelegate : 继承
    LayoutComboView --|> QListView : 继承

    CardUI *-- SettingsController
    SettingsController --> InferenceWorker : 等待回执后保存
    InferenceWorker ..> Command
    InferenceWorker ..> Response
    CardUI *-- InferenceWorker : 组合（创建并持有）
    CardUI o-- SettingsDialog : 聚合（按需创建）
    CardUI ..> trans_yolo_names_to_string : 依赖
    CardUI ..> settings : 依赖（读取/更新配置）

    SettingsDialog o-- LayoutEditorDialog : 聚合（按需创建）
    SettingsDialog *-- LayoutComboView : 组合
    SettingsDialog *-- LayoutItemDelegate : 组合
    SettingsDialog ..> settings : 依赖

    LayoutEditorDialog *-- RectCanvas : 组合
    LayoutEditorDialog o-- PreviewDialog : 聚合（按需创建）
    LayoutEditorDialog ..> service : 依赖
    LayoutEditorDialog ..> coord : 依赖
    LayoutEditorDialog ..> validator : 依赖

    InferenceWorker ..> GameController : 创建子进程实例
    GameController *-- ScreenCapture : 组合（创建并持有）
    GameController *-- YoloInferencer : 组合（创建并持有）
    GameController *-- LayoutAnalyzer : 手动分区
    GameController *-- AutoLayoutAnalyzer : 自动分组与跟踪
    AutoLayoutAnalyzer --> LayoutObservation : 已知区域与开局快照
    GameController *-- BaseCardTracker : 组合（通过 create_tracker() 创建）
    GameController *-- ImageSaver : 组合（创建并持有，统一管理）

    DoudizhuTracker --|> BaseCardTracker : 继承
    note right of DoudizhuTracker : 由 create_tracker() 根据 game_name 动态创建
    BaseCardTracker ..> settings : 依赖（状态常量/配置）

    YoloInferencer ..> settings : 依赖（模型路径/阈值/映射）
    YoloInferencer o-- ImageSaver : 聚合（由 GameController 传入）
```
