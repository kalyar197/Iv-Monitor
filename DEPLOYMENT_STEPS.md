# Google Cloud VM Setup - Next Steps

## Your Setup Details
- **VM Name**: binance-iv-monitor
- **GitHub Repo**: https://github.com/kalyar197/Iv-Monitor.git
- **Zone**: us-central1-a (adjust if different)

---

## STEP 1: SSH into Your VM

Go to Google Cloud Console and click the **SSH** button next to `binance-iv-monitor`

Or paste this in PowerShell (if you have gcloud installed):
```powershell
gcloud compute ssh binance-iv-monitor --zone=us-central1-a
```

---

## STEP 2: Clone Repository & Create .env File

**Paste this entire block** into the SSH terminal:

```bash
# Clone your repository
cd ~
git clone https://github.com/kalyar197/Iv-Monitor.git
cd Iv-Monitor

# Create .env file template
cat > .env << 'EOF'
BINANCE_API_KEY=YOUR_API_KEY_HERE
BINANCE_API_SECRET=YOUR_SECRET_HERE
DISCORD_WEBHOOK_URL=YOUR_WEBHOOK_HERE
EOF

# Show what was created
echo "=== .env file created. Now edit it with your real keys ==="
cat .env
```

**Then edit the .env file** with your actual API keys:
```bash
nano .env
```

- Replace `YOUR_API_KEY_HERE` with your Binance API key
- Replace `YOUR_SECRET_HERE` with your Binance API secret
- Replace `YOUR_WEBHOOK_HERE` with your Discord webhook URL

**Save**: Press `Ctrl+X`, then `Y`, then `Enter`

---

## STEP 3: Install System Dependencies

**Paste this entire block**:

```bash
# Update Ubuntu
sudo apt update && sudo apt upgrade -y

# Install Python, pip, and screen
sudo apt install -y python3-pip screen git

# Verify Python version (should be 3.10+)
python3 --version
```

---

## STEP 4: Install Python Dependencies

**Choose ONE option:**

### Option A: Simple (may show warning but works)
```bash
cd ~/Iv-Monitor
pip3 install -r requirements.txt
```

### Option B: Using venv (cleaner, recommended)
```bash
cd ~/Iv-Monitor
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

---

## STEP 5: Test Run (15 seconds)

```bash
cd ~/Iv-Monitor

# If you used venv:
source venv/bin/activate

# Test run
timeout 15 python3 -m src.main
```

**Expected output:**
```
✅ Running in SIMPLE mode - threshold-based alerts only
✅ Monitoring X symbols
✅ Starting IV monitoring (polling every 10s)
```

**If you see errors:**
- `ModuleNotFoundError`: Re-run pip install command
- `.env not found`: Make sure .env file exists with real API keys
- API errors: Check your API keys are correct

---

## STEP 6: Run 24/7 with Screen

**Start the monitor in background:**

```bash
cd ~/Iv-Monitor

# If using venv, activate it first:
source venv/bin/activate

# Start screen session
screen -S iv_monitor

# Run the monitor (runs forever)
python3 -m src.main
```

You should see logs scrolling:
```
Monitoring 60 symbols
Starting IV monitoring (polling every 10s)
Checking IV for 60/60 symbols
```

**Detach from screen** (leave it running in background):
- Press `Ctrl+A` (release both)
- Then press `D`
- You'll see: `[detached from iv_monitor]`

**The monitor is now running 24/7!**

---

## STEP 7: Verify It's Running

```bash
# Check screen sessions
screen -ls
# Should show: iv_monitor (Detached)

# Check process
ps aux | grep python
# Should show: python3 -m src.main
```

---

## STEP 8: Set Up Auto-Restart on Reboot

```bash
# Open crontab
crontab -e

# If asked to choose editor, select: 2 (nano)
```

**Add this line at the bottom** (adjust based on your setup):

**If you did NOT use venv:**
```bash
@reboot cd /home/YOUR_USERNAME/Iv-Monitor && screen -dmS iv_monitor python3 -m src.main
```

**If you used venv:**
```bash
@reboot cd /home/YOUR_USERNAME/Iv-Monitor && screen -dmS iv_monitor /home/YOUR_USERNAME/Iv-Monitor/venv/bin/python -m src.main
```

**Replace `YOUR_USERNAME`** - find it by running:
```bash
whoami
```

**Save crontab:**
- Press `Ctrl+X`
- Press `Y`
- Press `Enter`

---

## Quick Commands for Later

### View live logs
```bash
screen -r iv_monitor
# Detach: Ctrl+A then D
```

### Stop monitor
```bash
screen -X -S iv_monitor quit
```

### Start monitor
```bash
screen -S iv_monitor
cd ~/Iv-Monitor
source venv/bin/activate  # if using venv
python3 -m src.main
# Detach: Ctrl+A then D
```

### Update code from GitHub
```bash
cd ~/Iv-Monitor
git pull

# Restart monitor
screen -X -S iv_monitor quit
screen -S iv_monitor
python3 -m src.main
# Detach: Ctrl+A then D
```

### Check if running
```bash
screen -ls
ps aux | grep python
```

---

## Troubleshooting

**Can't find .env file?**
```bash
cd ~/Iv-Monitor
ls -la .env
nano .env  # Create/edit it
```

**Module not found errors?**
```bash
cd ~/Iv-Monitor
pip3 install --break-system-packages -r requirements.txt
```

**Monitor crashed?**
```bash
# Check logs
cd ~/Iv-Monitor
tail -f logs/iv_monitor.log

# Or attach to screen to see live output
screen -r iv_monitor
```

**Want to test locally first?**
```bash
# Run in foreground (Ctrl+C to stop)
cd ~/Iv-Monitor
python3 -m src.main
```

---

## That's It!

Once the monitor is running in screen, it will:
- ✅ Monitor BTC ATM options every 10 seconds
- ✅ Send Discord alerts when IV > 50% and OI > 10k
- ✅ Track progressive IV increases (alert at 60%, 61%, 62%, etc.)
- ✅ Auto-reset when IV drops 2% below baseline
- ✅ Run 24/7 in the background
- ✅ Auto-restart on VM reboot (after Step 8)

**Cost: $0/month** using Google Cloud free tier

**Check Discord for alerts!**
