import io
from lxml import etree
from collections import defaultdict
from datetime import datetime
from itertools import groupby

def analyze_chat_history(content, start_date=None, end_date=None):
    parser = etree.HTMLParser()
    tree = etree.parse(io.BytesIO(content), parser)

    stats = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: {"words": 0, "messages": 0, "attachments": 0})))
    total_messages = 0
    total_words = 0
    total_attachments = 0
    user_total_words = defaultdict(int)
    user_total_messages = defaultdict(int)
    user_total_attachments = defaultdict(int)
    user_ids = {}

    for msg in tree.xpath('//div[contains(@class, "chatlog__message-group")]'):
        try:
            author_elem = msg.xpath('.//span[@class="chatlog__author"]')
            if not author_elem:
                continue
            author = author_elem[0].get('title', 'Unknown')
            user_id = author_elem[0].get('data-user-id', 'Unknown')
            
            timestamp_elem = msg.xpath('.//span[@class="chatlog__timestamp"]/a/text()')
            if not timestamp_elem:
                continue
            timestamp = timestamp_elem[0]
            
            is_bot = bool(msg.xpath('.//span[@class="chatlog__author-tag" and text()="BOT"]'))
            
            # Skip bot messages
            if is_bot:
                continue

            # Handle both timestamp formats
            try:
                date = datetime.strptime(timestamp, '%d/%m/%Y %H:%M')
            except ValueError:
                try:
                    date = datetime.strptime(timestamp, '%d-%m-%Y %H:%M:%S')
                except ValueError:
                    continue  # Skip messages with invalid timestamps

            # Skip messages outside the specified date range
            if start_date and date < start_date:
                continue
            if end_date and date > end_date:
                continue

            # Extract content from regular messages and embeds, including formatted text
            content_elements = msg.xpath('.//div[contains(@class, "chatlog__content")]/span[@class="chatlog__markdown-preserve"]//text() | .//div[@class="chatlog__embed-description"]//div[@class="chatlog__markdown chatlog__markdown-preserve"]//text()')
            content = ' '.join(content_elements)

            word_count = len(content.split())

            # Count attachments
            attachment_count = len(msg.xpath('.//div[@class="chatlog__attachment"]/a'))

            stats[user_id][date.year][date.month]["words"] += word_count
            stats[user_id][date.year][date.month]["messages"] += 1
            stats[user_id][date.year][date.month]["attachments"] += attachment_count
            user_total_words[user_id] += word_count
            user_total_messages[user_id] += 1
            user_total_attachments[user_id] += attachment_count
            user_ids[user_id] = author
            total_messages += 1
            total_words += word_count
            total_attachments += attachment_count

            if total_messages % 1000 == 0:
                yield f"Processed {total_messages} messages..."
        except Exception as e:
            print(f"Error processing message: {e}")

    if total_messages == 0:
        yield "Error: No messages found in the chat log."
        return

    result = ["Chat Analysis Results:\n"]
    result.append(f"Total Messages: {total_messages}")
    result.append(f"Total Words: {total_words}")
    result.append(f"Total Attachments: {total_attachments}\n")

    # Remove headers from top 10 lists
    for user_id, count in sorted(user_total_words.items(), key=lambda x: x[1], reverse=True)[:10]:
        result.append(f"<@{user_id}>: {count} words")
    result.append("")

    for user_id, count in sorted(user_total_messages.items(), key=lambda x: x[1], reverse=True)[:10]:
        result.append(f"<@{user_id}>: {count} messages")
    result.append("")

    for user_id, count in sorted(user_total_attachments.items(), key=lambda x: x[1], reverse=True)[:10]:
        result.append(f"<@{user_id}>: {count} attachments")
    result.append("")

    for user_id, years in stats.items():
        result.append(f"User: <@{user_id}>")
        result.append(f"Total Messages: {user_total_messages[user_id]}")
        result.append(f"Total Words: {user_total_words[user_id]}")
        result.append(f"Total Attachments: {user_total_attachments[user_id]}")
        for year, months in sorted(years.items()):
            result.append(f"Year {year}:")
            for month, data in sorted(months.items()):
                result.append(f"  Month {month:02d}: {data['words']} words, {data['messages']} messages, {data['attachments']} attachments")
        result.append("")

    yield '\n'.join(result)
    yield user_ids

def process_chat_history(content, start_date=None, end_date=None):
    analyzer = analyze_chat_history(content, start_date, end_date)
    progress_updates = []
    final_result = ""
    user_ids = {}

    for item in analyzer:
        if isinstance(item, str) and item.startswith("Processed"):
            progress_updates.append(item)
        elif isinstance(item, dict):
            user_ids = item
        else:
            final_result = item

    return progress_updates, final_result, user_ids
