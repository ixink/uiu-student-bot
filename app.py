import logging
import sqlite3
import polars as pl
import random
import os
import json
import time
import asyncio
import subprocess
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import trafilatura
import aiohttp
from rapidfuzz import fuzz
import streamlit as st
import wikipediaapi
import wikipedia
from aiohttp import web
from typing import List, Dict

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://uiu-buddy-bot.onrender.com/webhook")
PORT = int(os.getenv("PORT", 8443))
STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", 8501))

# Initialize SQLite database
def init_db():
    try:
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS reminders (
            user_id INTEGER, task TEXT, deadline TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY, department TEXT, year INTEGER, favorite_roadmaps TEXT, courses TEXT, commute_location TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS study_plans (
            user_id INTEGER, courses TEXT, hours REAL, target_date TEXT, priorities TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS peer_matches (
            user_id INTEGER, course TEXT, location TEXT
        )''')
        # Insert mock data for peer matching
        mock_peers = [
            (1001, "Data Structures and Algorithms", "Mirpur"),
            (1002, "Data Structures and Algorithms", "Dhanmondi"),
            (1003, "Operating Systems", "Banani"),
            (1004, "Python Programming", "Gulshan"),
            (1005, "CSE321", "Uttara")
        ]
        c.executemany("INSERT OR IGNORE INTO peer_matches (user_id, course, location) VALUES (?, ?, ?)", mock_peers)
        conn.commit()
        logger.info("Database initialized successfully")
        return conn
    except sqlite3.OperationalError as e:
        logger.error(f"Database initialization failed: {e}")
        raise
    finally:
        if 'conn' in locals():
            conn.close()

init_db()

# Initialize Wikipedia APIs
wiki_api = wikipediaapi.Wikipedia('UIUBuddyBot/1.0 (https://uiu.ac.bd)', 'en')
wikipedia.set_lang("en")

# Temporary in-memory storage
temp_calendar: List[Dict] = []

# Mock data for fallback
MOCK_EVENTS = [
    {"name": "UIU Hackathon 2025", "date": "2025-09-15", "details": "Join the annual coding challenge!"},
    {"name": "Midterm Exams", "date": "2025-10-10", "details": "Prepare for your midterm exams."}
]
MOCK_ABOUT = (
    "UIU Developer Hub is a student-run club at United International University focused on fostering "
    "technical skills through workshops, hackathons, and coding competitions. It provides resources, "
    "mentorship, and networking opportunities for students in CSE and related fields."
)

# Web scraping with Trafilatura
async def fetch_web_content(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'}) as response:
                html = await response.text()
                return trafilatura.extract(html, include_links=True) or ""
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return ""

# Twitter/X scraping with snscrape
def scrape_twitter(query: str, max_results: int = 5) -> List[Dict]:
    try:
        cmd = f"snscrape --max-results {max_results} twitter-search '{query} near:Dhaka' > twitter_results.jsonl"
        subprocess.run(cmd, shell=True, check=True)
        results = []
        with open("twitter_results.jsonl", "r") as f:
            for line in f:
                data = json.loads(line)
                results.append({"user": data.get("user", {}).get("username", ""), "content": data.get("content", "")})
        return results
    except Exception as e:
        logger.error(f"Twitter scrape error: {e}")
        return []

# Rate limiting
user_last_scrape = {}
RATE_LIMIT_SECONDS = 30

def can_scrape(user_id):
    last_scrape = user_last_scrape.get(user_id, 0)
    if time.time() - last_scrape < RATE_LIMIT_SECONDS:
        return False
    user_last_scrape[user_id] = time.time()
    return True

# Command handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        keyboard = [
            [InlineKeyboardButton("View Profile", callback_data='view_profile'),
             InlineKeyboardButton("Set Profile", callback_data='set_profile'),
             InlineKeyboardButton("About UIU Developer Hub", callback_data='about'),
             InlineKeyboardButton("Help", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Welcome to UIU Buddy Bot! 🎓\n"
            f"Find study partners, ride shares, and more.",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Error starting the bot. Please try again.")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "📚 *UIU Buddy Bot Commands*\n\n"
            "1. **/start** - Start the bot and see welcome message.\n"
            "2. **/help** - Show this help message with all commands.\n"
            "3. **/about** - Learn about the UIU Developer Hub.\n"
            "4. **/calendar** - View UIU academic calendar events.\n"
            "5. **/resources [keyword]** - Find learning resources with Wikipedia summaries.\n"
            "   Example: /resources python\n"
            "6. **/cgpa <course:grade>** - Calculate CGPA.\n"
            "   Example: /cgpa cse321:A cse322:B+\n"
            "7. **/studyplan <courses> <hours> <date> <priority>** - Create a study plan.\n"
            "   Example: /studyplan cse321,cse322 10 2025-12-01 cse321:1,cse322:2\n"
            "8. **/reminders add <task> <date>** - Set reminders.\n"
            "   Example: /reminders add Meet Dr. Suman 2025-09-01\n"
            "9. **/reminders list** - List all reminders.\n"
            "10. **/motivate** - Get motivational tips.\n"
            "11. **/profile set <department> <year> <roadmaps> <courses> <commute>** - Set user profile.\n"
            "    Example: /profile set CSE 2 python,dsa cse321,cse322 Dhanmondi\n"
            "12. **/study find <course>** - Find peers taking a specific course.\n"
            "    Example: /study find cse321\n"
            "13. **/ride share <location>** - Find peers commuting to a location.\n"
            "    Example: /ride share Dhanmondi"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Error displaying help. Please try again.")

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting information again.")
            return

        content = await fetch_web_content("https://www.uiu.ac.bd/clubs/developer-hub")
        about_text = MOCK_ABOUT
        if content:
            lines = content.split('\n')
            for line in lines:
                if any(kw in line.lower() for kw in ['developer hub', 'club', 'mission', 'vision']):
                    about_text = line.strip()[:500] + "..." if len(line) > 500 else line.strip()
                    break

        response = (
            "🏫 *About UIU Developer Hub*\n\n"
            f"{about_text}\n\n"
            "Visit https://www.uiu.ac.bd/clubs/developer-hub for more details."
        )
        await update.message.reply_text(response, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in about command: {e}")
        await update.message.reply_text("Error fetching UIU Developer Hub info. Please try again.")

async def calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting another calendar.")
            return

        global temp_calendar
        temp_calendar = []
        content = await fetch_web_content("https://www.uiu.ac.bd/academic-calendars")
        if content:
            lines = content.split('\n')
            for line in lines[:5]:
                if any(kw in line.lower() for kw in ['event', 'seminar', 'holiday', 'exam']):
                    temp_calendar.append({"name": line.strip(), "date": "2025-09-15", "details": line.strip()})

        if temp_calendar:
            response = "UIU Academic Calendar Events:\n"
            for event in temp_calendar:
                response += f"- {event['name']} ({event['date']}): {event['details']}\n"
            keyboard = [[InlineKeyboardButton("Add Reminder", callback_data='add_reminder_calendar')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            response = "Unable to fetch calendar. Using mock data:\n"
            for event in MOCK_EVENTS:
                response += f"- {event['name']} ({event['date']}): {event['details']}\n"
            reply_markup = None
        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in calendar command: {e}")
        await update.message.reply_text("Error fetching calendar. Please try again.")

async def resources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        keyword = args[0].lower() if args else None
        if not can_scrape(user_id) and keyword:
            await update.message.reply_text("Please wait 30 seconds before requesting resources.")
            return

        resources = [
            {"title": "Learn Python", "platform": "freeCodeCamp", "link": "https://freecodecamp.org/learn/python"},
            {"title": "CS50", "platform": "edX", "link": "https://edx.org/course/cs50"},
            {"title": "JavaScript Tutorial", "platform": "w3schools", "link": "https://w3schools.com/js"},
            {"title": "Data Structures and Algorithms", "platform": "GeeksforGeeks", "link": "https://www.geeksforgeeks.org/data-structures"},
            {"title": "Operating Systems", "platform": "Coursera", "link": "https://www.coursera.org/learn/os"}
        ]

        wiki_summary = ""
        if keyword:
            page = wiki_api.page(keyword)
            if page.exists():
                wiki_summary = page.summary[:200] + "..." if len(page.summary) > 200 else page.summary
            else:
                try:
                    wiki_summary = wikipedia.summary(keyword, sentences=2)
                except:
                    wiki_summary = "No Wikipedia summary available."

        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = c.fetchone()
        finally:
            conn.close()

        response = f"Learning Resources{f' for {keyword}' if keyword else ''}:\n"
        filtered_resources = resources
        if keyword or (profile and profile[0]):
            filter_terms = [keyword] if keyword else profile[0].split(',')
            filtered_resources = [
                r for r in resources
                if any(fuzz.partial_ratio(term.lower(), r['title'].lower()) > 70 for term in filter_terms)
            ]
        for resource in filtered_resources[:5]:
            response += f"- {resource['title']} ({resource['platform']}): {resource['link']}\n"
        if wiki_summary:
            response += f"\nWikipedia Summary for {keyword}:\n{wiki_summary}"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in resources command: {e}")
        await update.message.reply_text("Error fetching resources. Please try again.")

async def cgpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage: /cgpa course1:grade1 course2:grade2\nExample: /cgpa cse321:A cse322:B+"
            )
            return
        grades = {course.split(':')[0]: course.split(':')[1] for course in args}
        grade_points = {'A': 4.0, 'A-': 3.7, 'B+': 3.3, 'B': 3.0, 'B-': 2.7, 'C+': 2.3, 'C': 2.0}
        df = pl.DataFrame({"Course": list(grades.keys()), "Grade": list(grades.values())})
        df = df.with_columns(pl.col("Grade").map_dict(grade_points, default=None).alias("Points"))
        if df["Points"].is_null().any():
            await update.message.reply_text("Invalid grade(s). Use: A, A-, B+, B, B-, C+, C")
            return
        cgpa = df["Points"].mean()
        await update.message.reply_text(f"Your CGPA: {cgpa:.2f}\n{df.select(['Course', 'Grade', 'Points']).to_string()}")
    except Exception as e:
        logger.error(f"Error in cgpa command: {e}")
        await update.message.reply_text("Invalid format. Use: /cgpa cse321:A cse322:B+")

async def studyplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 4:
            await update.message.reply_text(
                "Usage: /studyplan course1,course2 hours_per_week target_date(YYYY-MM-DD) priority(course1:1,cse322:2)"
            )
            return
        courses, hours, target_date, priority = args[0].split(','), float(args[1]), args[2], args[3]
        try:
            datetime.strptime(target_date, '%Y-%m-%d')
        except ValueError:
            await update.message.reply_text("Invalid date format. Use YYYY-MM-DD, e.g., 2025-09-01")
            return
        priorities = {p.split(':')[0]: int(p.split(':')[1]) for p in priority.split(',')}
        df = pl.DataFrame({"Course": courses})
        df = df.with_columns(
            Priority=pl.col("Course").map_dict(priorities, default=1),
            Hours=pl.col("Course").map_elements(lambda x: (hours * (3 - priorities.get(x, 1))) / len(courses))
        )
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("INSERT INTO study_plans (user_id, courses, hours, target_date, priorities) VALUES (?, ?, ?, ?, ?)",
                      (update.effective_user.id, args[0], hours, target_date, priority))
            conn.commit()
        finally:
            conn.close()
        response = f"Study Plan for {target_date}:\n{df.to_string()}\nTotal Hours/Week: {hours}"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in studyplan command: {e}")
        await update.message.reply_text("Error creating study plan. Please try again.")

async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /reminders add task deadline(YYYY-MM-DD) or /reminders list")
            return
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            if args[0].lower() == "add":
                task, deadline = " ".join(args[1:-1]), args[-1]
                try:
                    datetime.strptime(deadline, '%Y-%m-%d')
                except ValueError:
                    await update.message.reply_text("Invalid date format. Use YYYY-MM-DD, e.g., 2025-09-01")
                    return
                c.execute("INSERT INTO reminders (user_id, task, deadline) VALUES (?, ?, ?)",
                          (user_id, task, deadline))
                conn.commit()
                await update.message.reply_text(f"Reminder set: {task} on {deadline}")
            else:
                c.execute("SELECT task, deadline FROM reminders WHERE user_id = ?", (user_id,))
                reminders = c.fetchall()
                response = "Your Reminders:\n" + "\n".join(f"- {task} ({deadline})" for task, deadline in reminders)
                await update.message.reply_text(response or "No reminders set.")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error in reminders command: {e}")
        await update.message.reply_text("Error setting/listing reminders. Please try again.")

async def motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT favorite_roadmaps, department FROM user_profiles WHERE user_id = ?", (update.effective_user.id,))
            profile = c.fetchone()
        finally:
            conn.close()
        tips = [
            "Stay focused on your studies. Consistent effort leads to success!",
            "Break down complex topics into manageable parts. You can do it!",
            "Collaborate with peers to enhance your learning experience."
        ]
        if profile and profile[0]:
            tips.append(f"Keep exploring your {profile[0].split(',')[0]} skills. Progress adds up!")
        if profile and profile[1]:
            tips.append(f"Your {profile[1]} journey is shaping a bright future.")
        await update.message.reply_text(random.choice(tips))
    except Exception as e:
        logger.error(f"Error in motivate command: {e}")
        await update.message.reply_text("Error fetching motivational tip. Please try again.")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args or args[0].lower() != "set" or len(args) < 5:
            await update.message.reply_text(
                "Usage: /profile set department year favorite_roadmaps courses commute_location\n"
                "Example: /profile set CSE 2 python,dsa cse321,cse322 Dhanmondi"
            )
            return
        department, year, favorite_roadmaps, courses, commute_location = args[1], int(args[2]), args[3], args[4], args[5]
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO user_profiles (user_id, department, year, favorite_roadmaps, courses, commute_location) VALUES (?, ?, ?, ?, ?, ?)",
                      (user_id, department, year, favorite_roadmaps, courses, commute_location))
            c.execute("INSERT OR REPLACE INTO peer_matches (user_id, course, location) VALUES (?, ?, ?)",
                      (user_id, courses, commute_location))
            conn.commit()
        finally:
            conn.close()
        await update.message.reply_text(f"Profile updated: {department}, Year {year}, Roadmaps: {favorite_roadmaps}, Courses: {courses}, Commute: {commute_location}")
    except Exception as e:
        logger.error(f"Error in profile command: {e}")
        await update.message.reply_text("Error setting profile. Please try again.")

async def study_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /study find <course>\nExample: /study find cse321")
            return
        course = " ".join(args).lower()
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before searching for study partners.")
            return

        # Fetch from database
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT user_id, course, location FROM peer_matches WHERE lower(course) LIKE ?", (f"%{course}%",))
            matches = c.fetchall()
        finally:
            conn.close()

        # Twitter/X search
        twitter_results = scrape_twitter(f"{course} UIU", max_results=3)

        # Wikipedia summary
        wiki_summary = ""
        page = wiki_api.page(course)
        if page.exists():
            wiki_summary = page.summary[:200] + "..." if len(page.summary) > 200 else page.summary
        else:
            try:
                wiki_summary = wikipedia.summary(course, sentences=2)
            except:
                wiki_summary = "No Wikipedia summary available."

        response = f"Study Partners for {course}:\n"
        if matches:
            for match in matches:
                user_id, matched_course, location = match
                response += f"- User {user_id} taking {matched_course} (Location: {location})\n"
        else:
            response += "- No peers found in database.\n"

        if twitter_results:
            response += "\nTwitter/X Matches:\n"
            for result in twitter_results[:3]:
                response += f"- @{result['user']}: {result['content'][:100]}...\n"
        else:
            response += "\nNo Twitter/X matches found.\n"

        if wiki_summary:
            response += f"\nWikipedia Summary for {course}:\n{wiki_summary}"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in study find command: {e}")
        await update.message.reply_text("Error finding study partners. Please try again.")

async def ride_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /ride share <location>\nExample: /ride share Dhanmondi")
            return
        location = " ".join(args).lower()
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before searching for ride shares.")
            return

        # Fetch from database
        conn = sqlite3.connect('uiu_buddy.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT user_id, course, location FROM peer_matches WHERE lower(location) LIKE ?", (f"%{location}%",))
            matches = c.fetchall()
        finally:
            conn.close()

        # Twitter/X search
        twitter_results = scrape_twitter(f"commute {location} UIU", max_results=3)

        response = f"Ride Share Partners for {location}:\n"
        if matches:
            for match in matches:
                user_id, course, matched_location = match
                response += f"- User {user_id} (Courses: {course}, Location: {matched_location})\n"
        else:
            response += "- No peers found in database.\n"

        if twitter_results:
            response += "\nTwitter/X Matches:\n"
            for result in twitter_results[:3]:
                response += f"- @{result['user']}: {result['content'][:100]}...\n"
        else:
            response += "\nNo Twitter/X matches found.\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in ride share command: {e}")
        await update.message.reply_text("Error finding ride share partners. Please try again.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        if query.data == 'view_profile':
            user_id = query.from_user.id
            conn = sqlite3.connect('uiu_buddy.db', timeout=10)
            try:
                c = conn.cursor()
                c.execute("SELECT department, year, favorite_roadmaps, courses, commute_location FROM user_profiles WHERE user_id = ?", (user_id,))
                profile = c.fetchone()
            finally:
                conn.close()
            if profile:
                dept, year, roadmaps, courses, commute = profile
                await query.message.reply_text(f"Profile:\nDepartment: {dept}\nYear: {year}\nRoadmaps: {roadmaps}\nCourses: {courses}\nCommute: {commute}")
            else:
                await query.message.reply_text("No profile set. Use /profile set dept year roadmaps courses commute")
        elif query.data == 'set_profile':
            await query.message.reply_text(
                "Set your profile with /profile set department year favorite_roadmaps courses commute_location\n"
                "Example: /profile set CSE 2 python,dsa cse321,cse322 Dhanmondi"
            )
        elif query.data == 'add_reminder_calendar':
            await query.message.reply_text("To add a calendar event reminder, use /reminders add 'Event Name' YYYY-MM-DD")
        elif query.data == 'about':
            await about(update, context)
        elif query.data == 'help':
            await help(update, context)
    except Exception as e:
        logger.error(f"Error in button callback: {e}")
        await query.message.reply_text("Error processing your request. Please try again.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text("An error occurred. Please try again.")

# Streamlit dashboard
def run_streamlit():
    st.set_page_config(page_title="UIU Buddy Dashboard", page_icon="🎓")
    st.title("UIU Buddy Dashboard")
    st.header("Student Profiles, Study Plans, and Matches")

    conn = sqlite3.connect('uiu_buddy.db', timeout=10)
    try:
        profiles = pl.read_database("SELECT * FROM user_profiles", conn)
        if not profiles.is_empty():
            st.subheader("Student Profiles")
            st.dataframe(profiles)
        else:
            st.write("No user profiles found.")

        study_plans = pl.read_database("SELECT * FROM study_plans", conn)
        if not study_plans.is_empty():
            st.subheader("Study Plans")
            st.dataframe(study_plans)
        else:
            st.write("No study plans found.")

        reminders = pl.read_database("SELECT * FROM reminders", conn)
        if not reminders.is_empty():
            st.subheader("Reminders")
            st.dataframe(reminders)
        else:
            st.write("No reminders set.")

        peer_matches = pl.read_database("SELECT * FROM peer_matches", conn)
        if not peer_matches.is_empty():
            st.subheader("Peer Matches")
            st.dataframe(peer_matches)
        else:
            st.write("No peer matches found.")
    finally:
        conn.close()

# Webhook handler
async def webhook_handler(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        if update:
            await application.process_update(update)
        return web.Response(status=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return web.Response(status=500)

# Health check endpoint
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="UIU Buddy Bot is running", status=200)

# Setup Telegram application and webhook
async def setup_application():
    global application
    try:
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN environment variable not set")
            raise ValueError("BOT_TOKEN not set")

        application = Application.builder().token(BOT_TOKEN).build()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help))
        application.add_handler(CommandHandler("about", about))
        application.add_handler(CommandHandler("calendar", calendar))
        application.add_handler(CommandHandler("resources", resources))
        application.add_handler(CommandHandler("cgpa", cgpa))
        application.add_handler(CommandHandler("studyplan", studyplan))
        application.add_handler(CommandHandler("reminders", reminders))
        application.add_handler(CommandHandler("motivate", motivate))
        application.add_handler(CommandHandler("profile", profile))
        application.add_handler(CommandHandler("study", study_find))
        application.add_handler(CommandHandler("ride", ride_share))
        application.add_handler(CallbackQueryHandler(button_callback))
        application.add_error_handler(error_handler)

        await application.initialize()
        await application.start()

        webhook_info = await application.bot.get_webhook_info()
        if webhook_info.url != WEBHOOK_URL:
            await application.bot.delete_webhook(drop_pending_updates=True)
            await application.bot.set_webhook(url=WEBHOOK_URL)
            logger.info(f"Webhook set to {WEBHOOK_URL}")
        else:
            logger.info(f"Webhook already set to {WEBHOOK_URL}")
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        raise

# Main function
async def main():
    try:
        app = web.Application()
        app.router.add_post('/webhook', webhook_handler)
        app.router.add_get('/', health_check)

        await setup_application()

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"Webhook server running on port {PORT}")

        import subprocess
        subprocess.Popen(["streamlit", "run", __file__, "--server.port", str(STREAMLIT_PORT)])

        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    if "streamlit" in os.environ.get("PYTHONPATH", ""):
        run_streamlit()
    else:
        asyncio.run(main())
