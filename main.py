import os
import requests
from datetime import datetime
from flask import Flask, request, jsonify
import matplotlib
matplotlib.use('Agg')  # Required for server environments
import matplotlib.pyplot as plt
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

app = Flask(__name__)

# Slack setup
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
slack_client = WebClient(token=SLACK_BOT_TOKEN)

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

def generate_chart(data, filename="btc_l2_tvl.png"):
    """Generate a horizontal bar chart as PNG"""
    
    names = [d["name"] for d in reversed(data)]
    tvls = [d["tvl"] / 1_000_000 for d in reversed(data)]
    
    fig, ax = plt.subplots(figsize=(10, 8))
    fig.patch.set_facecolor('#1a1a2e')
    ax.set_facecolor('#1a1a2e')
    
    bars = ax.barh(names, tvls, color='#f7931a', edgecolor='#f7931a', height=0.7)
    
    for bar, tvl in zip(bars, tvls):
        width = bar.get_width()
        ax.text(width + max(tvls) * 0.02, bar.get_y() + bar.get_height()/2,
                f'${tvl:,.1f}M', va='center', ha='left', color='white', fontsize=11)
    
    ax.set_xlabel('TVL (USD Millions)', color='white', fontsize=12)
    ax.set_title(f'Bitcoin L2 TVL Rankings\n{datetime.now().strftime("%B %d, %Y")}', 
                 color='white', fontsize=16, fontweight='bold', pad=20)
    
    ax.tick_params(colors='white')
    ax.spines['bottom'].set_color('white')
    ax.spines['left'].set_color('white')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    
    for label in ax.get_yticklabels():
        label.set_color('white')
        label.set_fontsize(11)
    for label in ax.get_xticklabels():
        label.set_color('white')
    
    fig.text(0.5, 0.02, 'Data: DefiLlama | bitcoinlayers.org', 
             ha='center', color='gray', fontsize=9)
    
    plt.tight_layout()
    plt.savefig(filename, dpi=150, facecolor='#1a1a2e', edgecolor='none', 
                bbox_inches='tight', pad_inches=0.3)
    plt.close()
    
    return filename

@app.route('/', methods=['GET'])
def home():
    return "Bitcoin Layers Bot is running!"

@app.route('/slack/commands', methods=['POST'])
def slack_commands():
    """Handle /btclayers slash command"""
    
    command = request.form.get('command')
    text = request.form.get('text', '').strip().lower()
    channel_id = request.form.get('channel_id')
    
    if command == '/btclayers' and text == 'tvl':
        # Acknowledge immediately (Slack requires response within 3 seconds)
        # Then process and send the chart
        
        try:
            # Get data and generate chart
            data = get_bitcoin_l2_tvl()
            chart_file = generate_chart(data)
            
            # Upload chart to Slack
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
            return jsonify({'text': f"Error generating chart: {str(e)}"}), 200
    
    return jsonify({'text': 'Usage: /btclayers tvl'}), 200

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
