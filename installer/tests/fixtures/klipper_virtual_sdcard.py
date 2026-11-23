# The power-loss resume of QIDI Max 4 firmware 01.01.07.01 /home/qidi/klipper/klippy/extras/virtual_sdcard.py, its two
# methods copied unchanged; Max 4 01.01.06.05 and the Plus 5's and Smart 5's 01.01.02.01 have the same methods. The
# rest of the file is left out. Klipper is distributed under GNU GPLv3.
# Virtual sdcard support (print files directly from a host g-code file)
#
# Copyright (C) 2018-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, sys, logging, io, glob, json


class VirtualSD:
    def cmd_SDCARD_POWER_OFF_CONTINUES_PINT_FILE(self, gcmd):
        gcmd.respond_info("打印机恢复状态ing")
        if self.work_timer is not None:
            raise gcmd.error("SD busy")
        self._reset_file()
        import base64
        encoded_state = gcmd.get('STATE', '')
        try:
            state_data = json.loads(base64.b64decode(encoded_state).decode())
            p = state_data.get('params', {})
            logging.info(f"接收到的数据: {p}")
        except Exception as e:
            raise gcmd.error(f"解析状态包失败: {str(e)}")

        def _to_pos(val):
            if isinstance(val, list): return (val + [0.0]*4)[:4]
            return [0.0, 0.0, 0.0, 0.0]

        # 预计算位置变量
        base = _to_pos(p.get('base_position'))
        home = _to_pos(p.get('homing_position'))
        last = _to_pos(p.get('gcode_position'))

        # 直接调用修改后的方法
        self._set_printer_state_internal(p, last, base, home)

        plate_index = p.get('plate_index', '1')
        file_pos = int(float(p.get('file_position', 0))) # 双重保险转换

        self.has_run_m4050 = True # 恢复状态的打印，要禁止M4050
        search_pattern = os.path.join("/home/qidi/printer_data/.cache", "*")
        filename = glob.glob(search_pattern)[0]
        self._load_file(gcmd, filename, check_subdirs=True, plateindex=plate_index)
        # self.abort_epoch += 1
        self.file_position = max(file_pos - 4096, 0)
        self.do_resume()

    def _set_printer_state_internal(self, p, last, base, home):
        # --- 1. 数据解析 (保持现状) ---
        def _get_float(key, default=0.0):
            try: return float(p.get(key, default))
            except (ValueError, TypeError): return default
        def _get_bool(key, default=True):
            val = p.get(key, default)
            return str(val).lower() in ("true", "1", "yes") if not isinstance(val, bool) else val

        last_pos = (list(last) + [0.0]*4)[:4]
        is_abs_coord = _get_bool('absolute_coord', True)
        is_abs_extrude = _get_bool('absolute_extrude', True)

        # --- 2. 抽离定义类 G-Code (仅 Host 处理) ---
        define_lines = []
        all_objs = p.get('all_objects', [])
        for obj in [o for o in all_objs if isinstance(o, dict)]:
            name = obj.get('name')
            if not name: continue
            cmd = f"EXCLUDE_OBJECT_DEFINE NAME={name}"
            if obj.get('center'):
                c = obj['center']
                cmd += f" CENTER={c[0]},{c[1]}" if isinstance(c, (list, tuple)) else f" CENTER={c}"
            if obj.get('polygon'):
                cmd += f" POLYGON={json.dumps(obj['polygon'], separators=(',', ':'))}"
            define_lines.append(cmd)

        # --- 3. 构造状态与动作类 G-Code (涉及 MCU) ---
        action_lines = []
        # 排除状态
        excluded_objs = p.get('exclude_objects', [])
        action_lines.extend([f"EXCLUDE_OBJECT NAME={name}" for name in excluded_objs])
        
        # 硬件参数
        targets = {
            'ext': _get_float('extruder_target', 0.0),
            'bed': _get_float('heater_bed_target', 0.0),
            'chm': _get_float('chamber_target', 0.0),
            'f1':  _get_float('cooling_fan_speed', 0.0),
            'f2':  _get_float('auxiliary_cooling_fan_speed', 0.0),
            'f3':  _get_float('chamber_circulation_fan_speed', 0.0),
            'sf':  _get_float('speed_factor', 0.016667) * 60,
            'ef':  _get_float('extrude_factor', 1.0)
        }
        action_lines.extend([
            f"M140 S{targets['bed']:.2f}",
            f"M141 S{targets['chm']:.2f}",
            f"M104 S{targets['ext']:.2f}",
            f"M106 S{int(targets['f1'] * 255)}",
            f"M106 P2 S{int(targets['f2'] * 255)}",
            f"M106 P3 S{int(targets['f3'] * 255)}",
            f"M220 S{int(targets['sf'] * 100)}",
            f"M221 S{int(targets['ef'] * 100)}",
            # "G90" if is_abs_coord else "G91",
            # "M82" if is_abs_extrude else "M83"
        ])

        # 物理恢复动作
        # mesh_profile = p.get('bed_mesh_profile', 'default')
        mesh_profile = str(self.printer.lookup_object('save_variables').allVariables.get('profile_name', 'default'))
        # if mesh_profile:
        #     action_lines.append(f"BED_MESH_PROFILE LOAD={mesh_profile}")

        action_lines.extend([
            f"SET_KINEMATIC_POSITION X={last_pos[0]} Y={last_pos[1]} Z={last_pos[2]}",
            "G90",
            f"G1 Z{last_pos[2] + 5.0} F300",
            "M400", 
            "G28 X Y",
            f"BED_MESH_PROFILE LOAD={mesh_profile}" if mesh_profile else "",
            f"SET_GCODE_OFFSET X={home[0]} Y={home[1]} Z={home[2]}",
            f"CLEAR_NOZZLE_PLR HOTEND={targets['ext']:.2f}",
            f"G1 X{last_pos[0]} Y{last_pos[1]} F3000",
            f"M109 S{targets['ext']:.2f}",
            f"G1 Z{last_pos[2]} F300",
            "M82" if is_abs_extrude else "M83",
            f"G92 E{last_pos[3] if is_abs_extrude else 0}",
            "G90" if is_abs_coord else "G91",
            "M400"
        ])

        logging.info("--- PLR: DEFINE LINES ---\n" + "\n".join(define_lines))
        logging.info("--- PLR: ACTION LINES ---\n" + "\n".join(action_lines))

        # --- 4. 封装分步执行逻辑 ---
        def execute_script():
            logging.info("PLR: Starting phased execution")
            
            # 步骤 A: 执行定义 (分块下发，减少 Host 瞬间负载)
            chunk_size = 10
            for i in range(0, len(define_lines), chunk_size):
                chunk = define_lines[i : i + chunk_size]
                self.gcode.run_script_from_command("\n".join(chunk))   
            # 在定义和动作之间加一个 M400，确保 Host 完成对象解析同步
            self.gcode.run_script_from_command("M400")
            
            # 步骤 B: 执行物理恢复动作 (同样分块)
            for i in range(0, len(action_lines), chunk_size):
                chunk = action_lines[i : i + chunk_size]
                self.gcode.run_script_from_command("\n".join(chunk))
            # 同步 Python 对象
            gcode_move = self.printer.lookup_object('gcode_move')
            gcode_move.speed = _get_float('speed', 50.0)
            logging.info("PLR: Phased execution complete")

        self._printer_off_power_resume = execute_script
