# ADS-B Telegram Alerts

A Telegram Bot script built for ADS-B feeder instances (like ADSBExchange / `tar1090`) to automatically broadcast new flights to your Telegram channels.

It features multi-channel splitting (Civil vs Military), and a background retry process that actively monitors missing callsigns, airplane types, operator names, and pictures & smoothly updates the Telegram message as live data feeds in!

## 🔧 Prerequisites

Before installing, you will need to gather a few configuration values:

1. **Telegram Bot Token**:
   - Open Telegram and message **[@BotFather](https://t.me/botfather)**.
   - Send `/newbot`, choose a name and username.
   - BotFather will reply with an HTTP API Token (e.g. `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`). Copy this down.

2. **Telegram Chat IDs (Civil & Military)**:
   - Create two groups or channels in Telegram (one for global Civil traffic, one for Military).
   - Add your newly created Bot to both groups and promote the bot to **Admin**.
   - Send a normal text message in both groups from your own account.
   - Visit your browser and go to: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
   - In the JSON response, locate `"chat":{"id":-100...}`. These `-100...` numbers are your Chat IDs.

3. **ADS-B Exchange Feed ID**:
   - This is optional but used to generate personalized tracking links.
   - Obtain it from the [ADSBExchange Sync Portal](https://www.adsbexchange.com/myip/).

4. **Default Image Placeholder**:
   - Make sure you place a dummy image named `default.jfif` directly inside the script folder! This acts as an initial thumbnail until the script pulls down a real high-quality photo of the aircraft from the internet.

---

## ⚡ Option 1: Automated Installation (Recommended)

This project includes a convenient shell script that handles Python dependencies, an interactive configuration wizard, and automatic background systemd setup for Raspberry Pi!

1. Download or clone this repository to your Feeder.
2. Make the installer executable:
   ```bash
   chmod +x install.sh
   ```
3. Run the installer:
   ```bash
   ./install.sh
   ```
4. Follow the interactive **Configuration Wizard**. It will prompt you for your Token and Chat IDs, and write them directly into `all_aircraft.py`.
5. Choose **"yes"** when asked to install as a system service. It will automatically start up and boot on system restarts.

---

## 🛠️ Option 2: Manual Installation

If you prefer to configure things manually or are not using a standard Linux systemd OS:

1. **Install dependencies globally:**
   ```bash
   sudo apt-get update
   sudo apt-get install -y python3 python3-pip
   
   pip3 install requests pyTelegramBotAPI --break-system-packages
   ```

2. **Configure the script manually:**
   Open `all_aircraft.py` using your favorite code editor:
   ```bash
   nano all_aircraft.py
   ```
   Modify the `# ============== CONFIG ==============` section at the top of the file, replacing the `YOUR_TELEGRAM_BOT...` strings with your actual API keys.
   
3. **Run the bot:**
   ```bash
   python3 all_aircraft.py
   ```

---

## 🗑️ Uninstallation

If you used the automated installer and set it up as a system service, you can uninstall the background daemon with:

```bash
sudo systemctl stop adsb-alert
sudo systemctl disable adsb-alert
sudo rm /etc/systemd/system/adsb-alert.service
sudo systemctl daemon-reload
```

Afterward, you can safely wipe the directory.
