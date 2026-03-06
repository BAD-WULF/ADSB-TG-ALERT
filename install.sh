#!/bin/bash
# install.sh for ADSB Telegram Alert

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}Starting ADSB Telegram Alert Installation...${NC}"

# 1. Install dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
sudo apt-get update
sudo apt-get install -y python3 python3-pip

# Install required python packages globally
# Notice: some recent raspberry pi OS versions require --break-system-packages
pip3 install requests pyTelegramBotAPI --break-system-packages 2>/dev/null || pip3 install requests pyTelegramBotAPI

SCRIPT_DIR=$(pwd)
SERVICE_NAME="adsb-alert"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

configure_script() {
    echo -e "\n${YELLOW}--- Configuration Wizard ---${NC}"
    
    # TELEGRAM BOT TOKEN
    echo -e "\n${YELLOW}1. Telegram Bot Token${NC}"
    echo "You can get this by talking to @BotFather on Telegram and creating a new bot."
    read -p "Enter Telegram Bot Token (or press enter to cancel): " BOT_TOKEN
    if [ -z "$BOT_TOKEN" ]; then
        return 1
    fi

    # CIVIL CHAT ID
    echo -e "\n${YELLOW}2. Civil Chat ID${NC}"
    echo "Add your bot to a channel/group, make it admin. Send a test message, then visit: https://api.telegram.org/bot${BOT_TOKEN}/getUpdates"
    echo "Look for \"chat\":{\"id\":-100...} to get the ID."
    read -p "Enter Civil Chat ID (e.g. -10012345678) (or press enter to cancel): " CIVIL_ID
    if [ -z "$CIVIL_ID" ]; then
        return 1
    fi

    # MILITARY CHAT ID
    echo -e "\n${YELLOW}3. Military Chat ID${NC}"
    echo "This channel will only receive alerts for military aircraft. Get the ID the same way as the Civil ID."
    read -p "Enter Military Chat ID (or press enter to cancel): " MILITARY_ID
    if [ -z "$MILITARY_ID" ]; then
        return 1
    fi

    # FEED ID
    echo -e "\n${YELLOW}4. Feed ID${NC}"
    echo "Your personal ADS-B Exchange feed ID. Used for tracking on the Globe."
    echo "Found on the adsbexchange sync portal: https://www.adsbexchange.com/myip/"
    read -p "Enter Feed ID (or press enter to cancel): " FEED_ID
    if [ -z "$FEED_ID" ]; then
        return 1
    fi

    echo -e "\nApplying configuration to all_aircraft.py..."
    sed -i "s/TOKEN = \".*\"/TOKEN = \"$BOT_TOKEN\"/" "$SCRIPT_DIR/all_aircraft.py"
    sed -i "s/CIVIL_CHAT_ID = \".*\"/CIVIL_CHAT_ID = \"$CIVIL_ID\"/" "$SCRIPT_DIR/all_aircraft.py"
    sed -i "s/MILITARY_CHAT_ID = \".*\"/MILITARY_CHAT_ID = \"$MILITARY_ID\"/" "$SCRIPT_DIR/all_aircraft.py"
    sed -i "s/FEED_ID = \".*\"/FEED_ID = \"$FEED_ID\"/" "$SCRIPT_DIR/all_aircraft.py"

    echo -e "${GREEN}Configuration saved!${NC}"
    return 0
}

while true; do
    echo ""
    read -p "Do you want to run the configuration wizard? (y/n): " RUN_CONFIG
    if [[ "$RUN_CONFIG" =~ ^[Yy]$ ]]; then
        configure_script
        CONF_RES=$?
        
        if [ $CONF_RES -eq 1 ]; then
            read -p "Configuration aborted. Do you want to cancel the entire installation? (y/n): " CANCEL_INSTALL
            if [[ "$CANCEL_INSTALL" =~ ^[Yy]$ ]]; then
                echo -e "${RED}Installation cancelled.${NC}"
                exit 1
            fi
            continue
        fi
    fi

    echo ""
    read -p "Do you want to install this script as a system service so it runs automatically? (y/n): " INSTALL_S
    if [[ "$INSTALL_S" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Installing systemd service...${NC}"
        
        CURRENT_USER=$(whoami)
        
        sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=ADSB Telegram Alert Service
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/all_aircraft.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
        
        sudo systemctl daemon-reload
        sudo systemctl enable $SERVICE_NAME
        sudo systemctl restart $SERVICE_NAME
        
        echo -e "${GREEN}Service installed and started! Waiting 5 seconds to send test messages...${NC}"
        sleep 5
        
        echo ""
        read -p "Did you receive the test startup messages on Telegram? (y/n): " MSG_RECEIVED
        if [[ "$MSG_RECEIVED" =~ ^[Yy]$ ]]; then
            echo -e "${GREEN}Installation completely successful! 🎉${NC}"
            break
        else
            echo -e "${RED}Test messages not received.${NC}"
            read -p "Do you want to reconfigure (r) or cancel (c)? (r/c): " RECONF
            if [[ "$RECONF" =~ ^[Rr]$ ]]; then
                sudo systemctl stop $SERVICE_NAME
                continue
            else
                echo -e "${RED}Installation cancelled. Service stopped.${NC}"
                sudo systemctl stop $SERVICE_NAME
                sudo systemctl disable $SERVICE_NAME
                break
            fi
        fi
    else
        echo -e "${GREEN}Installation complete! You can run the script manually using: ${YELLOW}python3 all_aircraft.py${NC}"
        break
    fi
done
