import os
import time
import math
import struct
import threading
import queue
import re
import numpy as np
import requests
import wave
import json
import webbrowser
from datetime import datetime, timedelta
from dotenv import load_dotenv

import pyaudio
import pvporcupine
from groq import Groq

# KIVY IMPORTS
from kivy.app import App
from kivy.uix.floatlayout import FloatLayout
from kivy.uix.label import Label
from kivy.uix.widget import Widget
from kivy.graphics import Color, Line, Ellipse
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.utils import get_color_from_hex
from kivy.config import Config

# --- CONFIGURATION ---
Config.set('input', 'mouse', 'mouse,multitouch_on_demand')
load_dotenv()

# Set window background color
Window.clearcolor = (0.02, 0.03, 0.05, 1)

# API KEYS
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PICOVOICE_ACCESS_KEY = os.getenv("PICOVOICE_ACCESS_KEY")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# SETTINGS
CURRENT_LLM = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")
CURRENT_VOICE = os.getenv("VOICE_MODEL", "aura-asteria-en") 
WEATHER_CITY = os.getenv("WEATHER_CITY", "London")
SYSTEM_INSTRUCTIONS = os.getenv("SYSTEM_INSTRUCTIONS", "You are a helpful assistant. Keep answers concise.")

# FONTS 
FONT_DIGITAL = 'digital.ttf'
FONT_TEMP = 'temp.ttf'      
FONT_CLASSIC = 'classic.ttf' 

# AUDIO CONSTANTS
WAKE_WORD_KEYWORD = 'alexa' 
AUDIO_FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
INPUT_CHUNK = 512
OUTPUT_CHUNK = 4096 
VOLUME_GAIN = 3.0   

# ---------------------------------------------------------
# SHARED STATE
# ---------------------------------------------------------
class AssistantState:
    def __init__(self):
        self.active = False
        self.amplitude = 0.0
        self.stop_signal = False 
        self.interrupted = False 
        self.user_text = "" 
        self.ai_text = ""    
        self.status = ""
        self.current_temp = "??"
        self.alarms = [] 
        self.next_alarm_label = "No Active Alarms"
        self.is_alarm_ringing = False

state = AssistantState()

# ---------------------------------------------------------
# 1. TOOL MANAGER
# ---------------------------------------------------------
class ToolManager:
    @staticmethod
    def get_time(): 
        return datetime.now().strftime('%I:%M %p')

    @staticmethod
    def fetch_weather_bg():
        def _job():
            try:
                url = f"https://wttr.in/{WEATHER_CITY}?format=%t"
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    clean_temp = response.text.strip().replace('+', '')
                    state.current_temp = f"{clean_temp} in {WEATHER_CITY}"
                else: state.current_temp = "N/A"
            except: state.current_temp = "Offline"
        threading.Thread(target=_job, daemon=True).start()

    @staticmethod
    def parse_and_set_alarm(text):
        text = text.lower()
        now = datetime.now()
        target_time = None
        label = ""

        # Relative time
        match_min = re.search(r'in (\d+) minute', text)
        match_hr = re.search(r'in (\d+) hour', text)

        if match_min or match_hr:
            mins = int(match_min.group(1)) if match_min else 0
            hrs = int(match_hr.group(1)) if match_hr else 0
            target_time = now + timedelta(hours=hrs, minutes=mins)
            target_time = target_time.replace(second=0, microsecond=0)
            label = target_time.strftime("%I:%M %p").lstrip('0')
            full_fmt = target_time.strftime("%I:%M %p")

        # Absolute time
        else:
            regex = r'(\d{1,2})(?::(\d{2}))?\s*(am|pm|AM|PM)?'
            matches = re.findall(regex, text)
            valid_match = None
            for m in matches:
                if "alarm" in text or "wake" in text or "set" in text:
                    if m[0]: valid_match = m

            if valid_match:
                h = int(valid_match[0])
                m_str = valid_match[1].replace(':', '')
                m = int(m_str) if m_str else 0
                period = valid_match[2].lower()

                if h > 12: 
                    target_time = now.replace(hour=h, minute=m, second=0)
                else:
                    if period == 'pm' and h != 12: h += 12
                    elif period == 'am' and h == 12: h = 0
                    elif not period:
                        if now.hour > h: h += 12 

                    try: target_time = now.replace(hour=h, minute=m, second=0)
                    except: return None
                
                if target_time < now: target_time += timedelta(days=1)
                full_fmt = target_time.strftime("%I:%M %p")
        
        if target_time and full_fmt:
            user_friendly = full_fmt.lstrip('0') if full_fmt.startswith('0') else full_fmt
            standard_fmt = target_time.strftime("%I:%M %p").upper()
            state.alarms.append(standard_fmt)
            state.alarms.append(user_friendly)
            state.next_alarm_label = user_friendly
            return user_friendly
        return None

    @staticmethod
    def search_web(query):
        if not SERPER_API_KEY: return "API Key missing."
        try:
            resp = requests.post("https://google.serper.dev/search", 
                headers={'X-API-KEY': SERPER_API_KEY, 'Content-Type': 'application/json'}, 
                data=json.dumps({"q": query, "gl": "us"}), timeout=5).json()
            if 'organic' in resp: return resp['organic'][0].get('snippet')
            return "No results."
        except: return "Connection error."

    @staticmethod
    def play_on_youtube(query):
        try:
            # Requires `pip install pywhatkit` ideally, or simple webbrowser logic:
            webbrowser.open(f"https://www.youtube.com/results?search_query={query}")
            return True, "Opening YouTube..."
        except: return False, "Error opening YouTube."

# ---------------------------------------------------------
# 2. AUDIO ENGINE
# ---------------------------------------------------------
class AudioEngine:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self.stream = self.pa.open(
            format=AUDIO_FORMAT, channels=CHANNELS, rate=RATE, 
            output=True, frames_per_buffer=OUTPUT_CHUNK
        )
        self.audio_queue = queue.Queue()
        self.is_playing = False
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Token {DEEPGRAM_API_KEY}", "Content-Type": "application/json"})

    def get_mic_input_stream(self):
        return self.pa.open(format=AUDIO_FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=INPUT_CHUNK)

    def stop_playback(self):
        self.is_playing = False; state.amplitude = 0
        with self.audio_queue.mutex: self.audio_queue.queue.clear()

    def _playback_loop(self):
        self.is_playing = True
        while self.audio_queue.qsize() < 2 and self.is_playing: 
            time.sleep(0.05)
            if state.interrupted: break
        
        while self.is_playing:
            if state.interrupted: self.stop_playback(); break
            try:
                data = self.audio_queue.get(timeout=0.5)
                if data is None: break
                pcm = np.frombuffer(data, dtype=np.int16)
                boosted = np.clip(pcm.astype(np.float32) * VOLUME_GAIN, -32767, 32767).astype(np.int16)
                state.amplitude = float(np.mean(np.abs(boosted)) / 60)
                self.stream.write(boosted.tobytes())
            except: break
        self.is_playing = False; state.amplitude = 0

    def play_streamed_response(self, text):
        state.interrupted = False; self.stop_playback()
        self.is_playing = True 
        
        threading.Thread(target=self._playback_loop, daemon=True).start()
        url = f"https://api.deepgram.com/v1/speak?model={CURRENT_VOICE}&encoding=linear16&sample_rate={RATE}"
        try:
            with self.session.post(url, json={"text": text}, stream=True) as r:
                for chunk in r.iter_content(chunk_size=4096):
                    if state.interrupted: break
                    if chunk: self.audio_queue.put(chunk)
        except Exception as e: print(e)
        self.audio_queue.put(None)

    # --- RAW AUDIO PLAYBACK (WAV) ---
    def play_wav_once(self, data, params):
        def _job():
            try:
                p = pyaudio.PyAudio()
                s = p.open(format=p.get_format_from_width(params[1]), channels=params[0], rate=params[2], output=True)
                s.write(data)
                s.stop_stream(); s.close(); p.terminate()
            except: pass
        threading.Thread(target=_job, daemon=True).start()

    def play_alarm_loop(self, frames, params):
        def _loop():
            try:
                p = pyaudio.PyAudio()
                s = p.open(format=p.get_format_from_width(params[1]), channels=params[0], rate=params[2], output=True)
                for _ in range(20): 
                    if state.interrupted or not state.is_alarm_ringing: break
                    s.write(frames); time.sleep(0.2)
                s.stop_stream(); s.close(); p.terminate()
            except: pass
            state.is_alarm_ringing = False; state.next_alarm_label = "No Active Alarms"
        
        if not state.is_alarm_ringing:
            state.is_alarm_ringing = True
            threading.Thread(target=_loop, daemon=True).start()

# ---------------------------------------------------------
# 3. VISUALIZER & UI
# ---------------------------------------------------------
class ProAudioWave(Widget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.current_vol = 0; self.time = 0
        self.layers = [
            {"color": "#4287f5", "s": 1.0, "f": 1.0, "l": 0.0},
            {"color": "#00f7ff", "s": 1.5, "f": 1.5, "l": 0.5},
            {"color": "#8a00c2", "s": 2.2, "f": 2.0, "l": 1.0},
            {"color": "#ffffff", "s": 2.8, "f": 2.5, "l": 1.5},
        ]
        Clock.schedule_interval(self.update, 1.0 / 30.0)

    def update(self, dt):
        self.canvas.clear()
        if self.opacity <= 0: return
        self.time += dt; lerp = 0.2
        self.current_vol = self.current_vol * (1 - lerp) + state.amplitude * lerp
        amp = math.tanh(self.current_vol / 40.0) * (self.height * 0.3) + 5
        cy = self.center_y; w = self.width
        
        with self.canvas:
            for l in self.layers:
                r, g, b, _ = get_color_from_hex(l['color'])
                pts = []
                for x in range(0, int(w), 12):
                    nx = x/w
                    wy = math.sin(nx * 5 * l['f'] + self.time * l['s'] - l['l'])
                    y = cy + (wy * amp * math.sin(math.pi * nx))
                    pts.extend([x, y])
                Color(r, g, b, 0.8); Line(points=pts, width=2)

class AssistantInterface(FloatLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
        f_dig = FONT_DIGITAL if os.path.exists(FONT_DIGITAL) else None
        f_tmp = FONT_TEMP if os.path.exists(FONT_TEMP) else None
        f_cls = FONT_CLASSIC if os.path.exists(FONT_CLASSIC) else None
        
        self.time_lbl = Label(text="00:00", font_name=f_dig, font_size="130sp", bold=True, pos_hint={"center_x": 0.5, "center_y": 0.7})
        self.date_lbl = Label(text="DATE", font_name=f_dig, font_size="35sp", color=(0.5, 0.75, 0.9, 1), pos_hint={"center_x": 0.5, "center_y": 0.55})
        
        sub_col = (0.85, 0.85, 0.9, 0.8)
        self.weather_lbl = Label(text="Loading...", font_name=f_tmp, font_size="22sp", color=sub_col, pos_hint={"center_x": 0.5, "center_y": 0.15})
        self.alarm_lbl = Label(text="No alarms", font_name=f_tmp, font_size="22sp", color=sub_col, opacity=0, pos_hint={"center_x": 0.5, "center_y": 0.15})
        
        self.viz = ProAudioWave(opacity=0)
        self.stt_lbl = Label(text="", font_name=f_cls, font_size='22sp', color=(0.7,0.9,1,1), size_hint=(0.8, None), pos_hint={'center_x': 0.5, 'y': 0.6}, opacity=0)
        self.ai_lbl = Label(text="", font_name=f_cls, font_size='26sp', bold=True, color=(1,1,1,1), size_hint=(0.9, None), halign="center", valign="top", pos_hint={'center_x': 0.5, 'top': 0.45}, opacity=0)

        self.add_widget(self.viz)
        self.add_widget(self.time_lbl); self.add_widget(self.date_lbl)
        self.add_widget(self.weather_lbl); self.add_widget(self.alarm_lbl)
        self.add_widget(self.stt_lbl); self.add_widget(self.ai_lbl)
        
        self.dots = []; self.d_phase = 0
        with self.canvas:
            for _ in range(3):
                c = Color(0.4, 0.8, 1, 0); e = Ellipse(size=(10,10))
                self.dots.append((c,e))

        Clock.schedule_interval(self.update_sec, 1)
        Clock.schedule_interval(self.anim_loop, 1/60) 
        self.show_weather = True; self.info_timer = 0
        ToolManager.fetch_weather_bg()

    def update_sec(self, dt):
        now = datetime.now()
        self.time_lbl.text = now.strftime("%I:%M") 
        self.date_lbl.text = now.strftime("%A | %b %d").upper()
        self.weather_lbl.text = f"Temp: {state.current_temp}"
        
        if state.is_alarm_ringing:
            self.alarm_lbl.text = "!!! WAKE UP !!!"
            self.alarm_lbl.color = (1, 0.1, 0.1, 1) 
        else:
            self.alarm_lbl.text = f"Next Alarm: {state.next_alarm_label}"
            self.alarm_lbl.color = (0.85, 0.85, 0.9, 0.8)

        self.info_timer += 1
        if self.info_timer > 6:
            self.show_weather = not self.show_weather; self.info_timer = 0
        if state.active: self.info_timer = 0

    def anim_loop(self, dt):
        target_idle = 0.0 if state.active else 1.0
        self.time_lbl.opacity += (target_idle - self.time_lbl.opacity) * 0.1
        self.date_lbl.opacity = self.time_lbl.opacity
        
        self.viz.opacity += ((1-target_idle) - self.viz.opacity) * 0.2
        self.stt_lbl.opacity = self.viz.opacity
        self.ai_lbl.opacity = self.viz.opacity

        self.stt_lbl.text_size = (self.width * 0.8, None)
        self.ai_lbl.text_size = (self.width * 0.9, None)

        if not state.active:
            if self.show_weather:
                self.weather_lbl.opacity += (1 - self.weather_lbl.opacity) * 0.05
                self.alarm_lbl.opacity += (0 - self.alarm_lbl.opacity) * 0.1
            else:
                self.weather_lbl.opacity += (0 - self.weather_lbl.opacity) * 0.1
                self.alarm_lbl.opacity += (1 - self.alarm_lbl.opacity) * 0.05
        else:
            self.weather_lbl.opacity = 0; self.alarm_lbl.opacity = 0

        self.d_phase += dt * 2
        cy = self.height * 0.42; cx = self.center_x
        for i, (c, e) in enumerate(self.dots):
            c.a = (math.sin(self.d_phase - i) + 1)/2 * 0.4 * target_idle
            e.pos = (cx - 30 + i*30 - 5, cy)
            
        # Typewriter Logic
        if state.active:
            tgt_u = f"You: {state.user_text}" if state.user_text else ""
            if self.stt_lbl.text != tgt_u:
                self.stt_lbl.text = tgt_u[:len(self.stt_lbl.text)+2]
            
            tgt_a = state.ai_text
            if not tgt_a.startswith(self.ai_lbl.text) and self.ai_lbl.text != "":
                 if len(tgt_a) > 0 and len(self.ai_lbl.text) > 0 and tgt_a[0] != self.ai_lbl.text[0]:
                    self.ai_lbl.text = "" 

            if self.ai_lbl.text != tgt_a:
                self.ai_lbl.text = tgt_a[:len(self.ai_lbl.text)+1]

# ---------------------------------------------------------
# 4. MAIN LOGIC
# ---------------------------------------------------------
class SmartAssistant:
    def __init__(self):
        self.engine = AudioEngine()
        self.groq = Groq(api_key=GROQ_API_KEY)
        self.porcupine = None
        self.beep_raw = None; self.beep_p = None

        if os.path.exists("beep.wav"):
            try:
                with wave.open("beep.wav", 'rb') as wf:
                    self.beep_p = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
                    self.beep_raw = wf.readframes(wf.getnframes())
            except: pass

        try: self.porcupine = pvporcupine.create(access_key=PICOVOICE_ACCESS_KEY, keywords=[WAKE_WORD_KEYWORD])
        except: pass
        
        threading.Thread(target=self.loop, daemon=True).start()
        threading.Thread(target=self.alarm_checker, daemon=True).start()

    # --- NEW METHOD: Force window to front ---
    def bring_window_front(self):
        def _job(dt):
            try:
                if hasattr(Window, 'restore'): Window.restore()
                Window.raise_window()
            except: pass
        # Wait 3 seconds for browser to load, then steal focus back
        Clock.schedule_once(_job, 3.0)

    def alarm_checker(self):
        while True:
            now_full = datetime.now().strftime("%I:%M %p").upper()
            current_alarms = list(state.alarms) 
            triggered = False
            for alarm_time in current_alarms:
                if alarm_time == now_full:
                    triggered = True
                    if alarm_time in state.alarms: state.alarms.remove(alarm_time)
            
            if triggered:
                state.active = True; state.ai_text = "ALARM RINGING"
                if self.beep_raw: self.engine.play_alarm_loop(self.beep_raw, self.beep_p)
            time.sleep(1)

    def loop(self):
        pa = pyaudio.PyAudio()
        try: mic = pa.open(rate=self.porcupine.sample_rate, channels=1, format=pyaudio.paInt16, input=True, frames_per_buffer=self.porcupine.frame_length)
        except: return 
        print("Listening...")
        
        while not state.stop_signal:
            try:
                pcm = mic.read(self.porcupine.frame_length, exception_on_overflow=False)
                pcm = struct.unpack_from("h" * self.porcupine.frame_length, pcm)
                is_wake = self.porcupine.process(pcm) >= 0
                
                if is_wake or state.is_alarm_ringing:
                    if state.is_alarm_ringing: state.interrupted = True 
                    if is_wake and not state.is_alarm_ringing and self.beep_raw:
                        self.engine.play_wav_once(self.beep_raw, self.beep_p)
                        time.sleep(0.5)
                    self.conversation()
            except: pass

    def conversation(self):
        state.active = True; state.status = "Listening"
        state.user_text = ""; state.ai_text = ""
        
        mic = self.engine.get_mic_input_stream()
        frames = []; silence = 0; speaking = False
        
        for _ in range(0, int(RATE / INPUT_CHUNK * 6)):
            data = mic.read(INPUT_CHUNK, exception_on_overflow=False)
            frames.append(data)
            amp = np.mean(np.abs(np.frombuffer(data, dtype=np.int16)))
            state.amplitude = amp / 30
            if amp > 500: speaking = True; silence = 0
            if speaking: silence += 1 if amp < 300 else 0
            if silence > 30: break
            
        mic.stop_stream(); mic.close()
        state.status = "Thinking..."
        
        try:
            with wave.open("temp.wav", 'wb') as wf:
                wf.setnchannels(CHANNELS); wf.setsampwidth(2); wf.setframerate(RATE)
                wf.writeframes(b''.join(frames))
            with open("temp.wav", "rb") as f:
                user_txt = self.groq.audio.transcriptions.create(file=("temp.wav", f.read()), model="whisper-large-v3").text
            state.user_text = user_txt
        except: user_txt = ""

        if not user_txt.strip(): 
            state.active = False; return

        rsp = ""
        l_txt = user_txt.lower()
        opened_external = False # Flag to track external apps

        if "alarm" in l_txt or "wake me" in l_txt:
            pt = ToolManager.parse_and_set_alarm(user_txt)
            rsp = f"Alarm set for {pt}." if pt else "Please say a time, like '5 PM'."
        
        elif "time" in l_txt: rsp = f"It's {ToolManager.get_time()}"
        elif "search" in l_txt: rsp = ToolManager.search_web(user_txt)
        
        elif "play" in l_txt: 
            success, rsp = ToolManager.play_on_youtube(l_txt.replace("play","").strip())
            if success: opened_external = True # Set flag true

        if not rsp:
            msgs = [{"role": "system", "content": SYSTEM_INSTRUCTIONS}, {"role": "user", "content": user_txt}]
            try: rsp = self.groq.chat.completions.create(model=CURRENT_LLM, messages=msgs, max_tokens=200).choices[0].message.content
            except: rsp = "Error generating response."

        state.ai_text = ""
        state.status = "Speaking"
        state.ai_text = rsp
        self.engine.play_streamed_response(rsp)

        # TRIGGER WINDOW RESTORE IF EXTERNAL APP OPENED
        if opened_external:
            self.bring_window_front()

        time.sleep(0.5) 
        while self.engine.is_playing and not state.interrupted:
            time.sleep(0.1)

        state.active = False

# ---------------------------------------------------------
# APP
# ---------------------------------------------------------
class VoiceApp(App):
    def build(self): return AssistantInterface()
    def on_start(self): self.assistant = SmartAssistant()
    def on_stop(self): state.stop_signal = True; os._exit(0)

if __name__ == '__main__':
    VoiceApp().run()