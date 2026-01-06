import os
import requests
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)

# Slack setup
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# Database setup
DATABASE_URL = os.environ.get('DATABASE_URL')

def get_db_connection():
    """Get database connection"""
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    """Create tables if they don't exist"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tvl_snapshots (
            id SERIAL PRIMARY KEY,
            snapshot_date DATE NOT NULL,
            chain_name VARCHAR(100) NOT NULL,
            tvl_usd NUMERIC NOT NULL,
            rank INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(snapshot_date, chain_name)
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

def get_bitcoin_l2_tvl():
    """Fetch Bitcoin L2 TVL data from DefiLlama"""
    url = "https://api.llama.fi/v2/chains"
    response = requests.get(url)
    data = response.json()
    
    bitcoin_l2s = [
        "Core", "Bitlayer", "Bsquared", "BOB", "Rootstock", 
        "Merlin", "Stacks", "AILayer", "BounceBit", "MAP Protocol",
        "BEVM", "Liquid", "Lightning"
    ]
    
    results = []
    for chain in data:
        if chain.get("name") in bitcoin_l2s:
            results.append({
                "name": chain.get("name"),
                "tvl": chain.get("tvl", 0)
            })
    
    results.sort(key=lambda x: x["tvl"], reverse=True)
    return results[:10]

def save_snapshot(data, snapshot_date=None):
    """Save TVL snapshot to database"""
    if snapshot_date is None:
        snapshot_date = datetime.now().date()
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    for rank, chain in enumerate(data, 1):
        cur.execute('''
            INSERT INTO tvl_snapshots (snapshot_date, chain_name, tvl_usd, rank)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (snapshot_date, chain_name) 
            DO UPDATE SET tvl_usd = EXCLUDED.tvl_usd, rank = EXCLUDED.rank
        ''', (snapshot_date, chain['name'], chain['tvl'], rank))
    
    conn.commit()
    cur.close()
    conn.close()

def get_previous_snapshot(days_ago=1):
    """Get snapshot from N days ago"""
    target_date = datetime.now().date() - timedelta(days=days_ago)
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Get the most recent snapshot on or before target_date
    cur.execute('''
        SELECT DISTINCT snapshot_date FROM tvl_snapshots 
        WHERE snapshot_date <= %s 
        ORDER BY snapshot_date DESC 
        LIMIT 1
    ''', (target_date,))
    
    result = cur.fetchone()
    if not result:
        cur.close()
        conn.close()
        return None, None
    
    prev_date = result['snapshot_date']
    
    cur.execute('''
        SELECT chain_name, tvl_usd, rank FROM tvl_snapshots 
        WHERE snapshot_date = %s
        ORDER BY rank
    ''', (prev_date,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    return prev_date, {row['chain_name']: {'tvl': float(row['tvl_usd']), 'rank': row['rank']} for row in rows}

def calculate_changes(current_data, previous_data):
    """Calculate TVL changes and rank movements"""
    results = []
    
    for rank, chain in enumerate(current_data, 1):
        name = chain['name']
        tvl = chain['tvl']
        
        change = 0
        change_pct = 0
        rank_change = 0
        prev_rank = None
        
        if previous_data and name in previous_data:
            prev = previous_data[name]
            change = tvl - prev['tvl']
            if prev['tvl'] > 0:
                change_pct = (change / prev['tvl']) * 100
            prev_rank = prev['rank']
            rank_change = prev_rank - rank  # Positive = moved up
        
        results.append({
            'name': name,
            'tvl': tvl,
            'rank': rank,
            'change': change,
            'change_pct': change_pct,
            'rank_change': rank_change,
            'prev_rank': prev_rank,
            'is_new': previous_data is not None and name not in previous_data
        })
    
    return results

def get_notable_events(data):
    """Identify notable events for callouts"""
    events = []
    
    # Filter out entries with no change data
    with_changes = [d for d in data if d['change'] != 0 or d['is_new']]
    
    if not with_changes:
        return events
    
    # Biggest gainer (by percentage)
    gainers = [d for d in with_changes if d['change_pct'] > 0]
    if gainers:
        biggest_gainer = max(gainers, key=lambda x: x['change_pct'])
        rank_note = ""
        if biggest_gainer['rank_change'] >= 2:
            rank_note = f" (jumped {biggest_gainer['rank_change']} spots)"
        elif biggest_gainer['rank_change'] == 1:
            rank_note = " (up 1 spot)"
        events.append(f"🔥 BIGGEST GAINER: {biggest_gainer['name']} +{biggest_gainer['change_pct']:.1f}%{rank_note}")
    
    # Biggest loser (by percentage)
    losers = [d for d in with_changes if d['change_pct'] < 0]
    if losers:
        biggest_loser = min(losers, key=lambda x: x['change_pct'])
        events.append(f"📉 BIGGEST LOSER: {biggest_loser['name']} {biggest_loser['change_pct']:.1f}%")
    
    # New to top 10
    new_entries = [d for d in data if d['is_new']]
    for entry in new_entries:
        events.append(f"🆕 NEW TO TOP 10: {entry['name']}")
    
    # Big rank jumps (2+ spots)
    big_movers = [d for d in data if d['rank_change'] >= 2]
    for mover in big_movers:
        if mover['name'] != (gainers[0]['name'] if gainers else None):  # Don't duplicate biggest gainer
            events.append(f"⬆️ {mover['name']} jumped {mover['rank_change']} spots")
    
    return events[:4]  # Max 4 events

def generate_chart(data, filename="btc_l2_tvl.png"):
    """Generate chart matching the mockup design"""
    
    # Set up the figure with dark background
    fig, ax = plt.subplots(figsize=(12, 10))
    fig.patch.set_facecolor('#2d2d2d')
    ax.set_facecolor('#2d2d2d')
    
    # Prepare data (reversed for bottom-to-top display)
    names = [d['name'] for d in reversed(data)]
    tvls = [d['tvl'] / 1_000_000 for d in reversed(data)]
    changes = list(reversed(data))
    
    # Create horizontal bars
    y_pos = range(len(names))
    bars = ax.barh(y_pos, tvls, color='#a0a0a0', height=0.6)
    
    # Remove axes
    ax.set_yticks([])
    ax.set_xticks([])
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.spines['bottom'].set_visible(False)
    ax.spines['left'].set_visible(False)
    
    # Add labels for each bar
    max_tvl = max(tvls) if tvls else 1
    for i, (bar, chain_data) in enumerate(zip(bars, changes)):
        rank = len(data) - i
        name = chain_data['name']
        tvl = chain_data['tvl'] / 1_000_000
        change = chain_data['change'] / 1_000_000
        change_pct = chain_data['change_pct']
        rank_change = chain_data['rank_change']
        
        # Rank and name on left
        ax.text(-max_tvl * 0.02, bar.get_y() + bar.get_height()/2,
                f"{rank}. {name}", va='center', ha='right', color='white', fontsize=11, fontweight='bold')
        
        # TVL value after bar
        ax.text(bar.get_width() + max_tvl * 0.02, bar.get_y() + bar.get_height()/2,
                f"${tvl:,.1f}M", va='center', ha='left', color='white', fontsize=11)
        
        # Change values
        if change != 0:
            change_str = f"+{change:,.1f}" if change > 0 else f"{change:,.1f}"
            pct_str = f"(+{change_pct:.1f}%)" if change_pct > 0 else f"({change_pct:.1f}%)"
            change_color = '#4ade80' if change > 0 else '#f87171'
            
            ax.text(bar.get_width() + max_tvl * 0.20, bar.get_y() + bar.get_height()/2,
                    f"{change_str}  {pct_str}", va='center', ha='left', color=change_color, fontsize=10)
        
        # Rank change arrows
        if rank_change != 0:
            if rank_change >= 2:
                arrow = "↑↑"
            elif rank_change == 1:
                arrow = "↑"
            elif rank_change == -1:
                arrow = "↓"
            else:
                arrow = "↓↓"
            arrow_color = '#4ade80' if rank_change > 0 else '#f87171'
            ax.text(bar.get_width() + max_tvl * 0.45, bar.get_y() + bar.get_height()/2,
                    arrow, va='center', ha='left', color=arrow_color, fontsize=12, fontweight='bold')
    
    # Title
    ax.text(0.5, 1.08, "BITCOIN L2 TVL RANKINGS", transform=ax.transAxes,
            fontsize=16, fontweight='bold', color='white', ha='center')
    ax.text(0.5, 1.03, datetime.now().strftime("%B %d, %Y"), transform=ax.transAxes,
            fontsize=12, color='#a0a0a0', ha='center')
    
    # Calculate totals
    total_tvl = sum(d['tvl'] for d in data) / 1_000_000
    total_change = sum(d['change'] for d in data) / 1_000_000
    total_change_pct = (total_change / (total_tvl - total_change) * 100) if (total_tvl - total_change) > 0 else 0
    
    # Divider line
    ax.axhline(y=-0.8, color='#505050', linewidth=1, xmin=0.1, xmax=0.9)
    
    # Total line
    change_sign = "+" if total_change >= 0 else ""
    ax.text(0.5, -0.06, f"Total: ${total_tvl:,.0f}M  •  {change_sign}{total_change:,.0f} ({change_sign}{total_change_pct:.1f}%) vs yesterday",
            transform=ax.transAxes, fontsize=11, color='white', ha='center')
    
    # Notable events
    events = get_notable_events(data)
    for i, event in enumerate(events):
        ax.text(0.5, -0.12 - (i * 0.045), event, transform=ax.transAxes,
                fontsize=10, color='#a0a0a0', ha='center')
    
    # Footer
    ax.text(0.5, -0.32, "Data: bitcoinlayers.org", transform=ax.transAxes,
            fontsize=9, color='#606060', ha='center')
    
    # Adjust layout
    plt.xlim(-max_tvl * 0.35, max_tvl * 1.5)
    plt.subplots_adjust(left=0.25, right=0.95, top=0.88, bottom=0.25)
    
    plt.savefig(filename, dpi=150, facecolor='#2d2d2d', edgecolor='none',
                bbox_inches='tight', pad_inches=0.5)
    plt.close()
    
    return filename

@app.route('/', methods=['GET'])
def home():
    return "Bitcoin Layers Bot is running!"

@app.route('/init-db', methods=['GET'])
def initialize_database():
    """Initialize database tables"""
    try:
        init_db()
        return jsonify({'status': 'success', 'message': 'Database initialized'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/slack/commands', methods=['POST'])
def slack_commands():
    """Handle /btclayers slash command"""
    
    command = request.form.get('command')
    text = request.form.get('text', '').strip().lower()
    channel_id = request.form.get('channel_id')
    
    if command == '/btclayers' and text == 'tvl':
        try:
            # Get current data
            current_data = get_bitcoin_l2_tvl()
            
            # Get previous snapshot
            prev_date, previous_data = get_previous_snapshot(days_ago=1)
            
            # Calculate changes
            data_with_changes = calculate_changes(current_data, previous_data)
            
            # Save today's snapshot
            save_snapshot(current_data)
            
            # Generate chart
            chart_file = generate_chart(data_with_changes)
            
            # Upload to Slack
            response = slack_client.files_upload_v2(
                channel=channel_id,
                file=chart_file,
                title=f"Bitcoin L2 TVL Rankings - {datetime.now().strftime('%Y-%m-%d')}",
                initial_comment="Here's the latest Bitcoin L2 TVL data:"
            )
            
            return '', 200
            
        except SlackApiError as e:
            print(f"Slack API error: {e.response['error']}")
            return jsonify({'text': f"Error: {e.response['error']}"}), 200
        except Exception as e:
            print(f"Error: {str(e)}")
            return jsonify({'text': f"Error: {str(e)}"}), 200
    
    return jsonify({'text': 'Usage: /btclayers tvl'}), 200

@app.route('/daily-post', methods=['POST', 'GET'])
def daily_post():
    """Post daily TVL chart to channel"""
    
    CHANNEL_ID = 'C0A6HT4PZMH'
    
    try:
        # Get current data
        current_data = get_bitcoin_l2_tvl()
        
        # Get previous snapshot
        prev_date, previous_data = get_previous_snapshot(days_ago=1)
        
        # Calculate changes
        data_with_changes = calculate_changes(current_data, previous_data)
        
        # Save today's snapshot
        save_snapshot(current_data)
        
        # Generate chart
        chart_file = generate_chart(data_with_changes)
        
        # Upload to Slack
        response = slack_client.files_upload_v2(
            channel=CHANNEL_ID,
            file=chart_file,
            title=f"Bitcoin L2 TVL Rankings - {datetime.now().strftime('%Y-%m-%d')}",
            initial_comment="☀️ Good morning! Here's your daily Bitcoin L2 TVL update:"
        )
        
        return jsonify({'status': 'success'}), 200
        
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
