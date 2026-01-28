# 🎙️ Offline / Real-Time Voice Assistant

A smart AI-powered voice assistant built using **Python**, **ESP32 / Raspberry Pi concepts**, and modern **AI + Voice APIs**.

---

## 📸 Screenshots

### 🟢 Idle Mode (Front Screen)
The assistant waiting for the wake word.  
![Idle Mode](screenshots/1.png)

### 🎧 Listening Mode (Wake Word Detected)
Activated after the wake word is heard.  
![Listening Mode](screenshots/2.png)

### 🧠 Speech-to-Text & AI Response
User speech converted to text and answered by the AI.  
![STT and AI Response](screenshots/3.png)

---

## 🚀 Features
- Wake word detection (Porcupine)
- Real-time Speech-to-Text (Whisper / Groq)
- LLM-based intelligent responses
- Text-to-Speech with streaming audio
- Alarm system
- Weather information
- YouTube playback
- Beautiful real-time Kivy-based UI

---

## 🧠 Tech Stack

### 💻 Software
- Python
- Kivy
- Groq LLM
- Whisper STT
- Deepgram TTS
- REST APIs

### 🔌 Hardware (Concept & Integration Ready)
- ESP32
- Raspberry Pi
- Microphones & sensors

---

## 📦 Setup

### 1️⃣ Install Dependencies
```bash
pip install -r requirements.txt
```

---

### 2️⃣ Environment Variables
Create a `.env` file using the example provided:

```bash
cp .env.example .env
```

Fill in your API keys inside `.env`.

---

### 3️⃣ Run the Assistant
```bash
python main.py
```

---

## ⚠️ Notes
- Requires a microphone and speaker
- Best tested on Linux / Raspberry Pi
- Python 3.9+ recommended
- `.env` file should **never** be pushed to GitHub

---

## 👤 Author
**Saiyam (Age 15)**  
AI & Backend Developer | Hardware + Software Explorer  

---

⭐ Feel free to explore the repositories  
📫 Let’s build something awesome
