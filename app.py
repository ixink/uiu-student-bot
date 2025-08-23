import logging
import sqlite3
import polars as pl
import random
import os
import json
import time
import asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import trafilatura
import aiohttp
from rapidfuzz import fuzz
import streamlit as st
import wikipediaapi
from aiohttp import web
from typing import List, Dict
from urllib.parse import urljoin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://uiu-student-bot.onrender.com/webhook")
PORT = int(os.getenv("PORT", 8443))
STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", 8501))

# Initialize SQLite database
def init_db():
    try:
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS reminders (
            user_id INTEGER, task TEXT, deadline TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS code_snippets (
            user_id INTEGER, description TEXT, tags TEXT, snippet TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY, department TEXT, year INTEGER, favorite_roadmaps TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS progress (
            user_id INTEGER, roadmap_type TEXT, level TEXT, completed_steps TEXT
        )''')
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

# Initialize Wikipedia API
wiki = wikipediaapi.Wikipedia('UIUDeveloperHubBot/1.0 (https://uiu.ac.bd)', 'en')

# Temporary in-memory storage
temp_calendar: List[Dict] = []
temp_resources: List[Dict] = []
temp_trending: List[Dict] = []
temp_stackoverflow: List[Dict] = []
temp_jobs: List[Dict] = []
temp_notices: List[Dict] = []
temp_faculty: List[Dict] = []

# Mock data for fallback
MOCK_NOTICES = [
    {"title": "Class Schedule Update", "date": "2025-08-20", "details": "New schedule for Fall 2025 released."},
    {"title": "Exam Postponed", "date": "2025-08-22", "details": "Midterm exams rescheduled to Nov 2025."}
]
MOCK_EVENTS = [
    {"name": "UIU Hackathon 2025", "date": "2025-09-15", "details": "Join the annual coding challenge!"}
]
MOCK_SCHOLARSHIPS = [
    {"name": "UIU Merit Scholarship", "details": "For students with CGPA > 3.5", "link": "http://www.uiu.ac.bd"}
]
MOCK_ROADMAP = {
    "title": "Generic Roadmap",
    "steps": ["Learn basics", "Practice projects"],
    "resources": ["Online tutorials", "Official documentation"],
    "projects": ["Build a small app"]
}
MOCK_FACULTY = [
    {"name": "Dr. Suman Ahmmed", "designation": "Head", "department": "CSE", "email": "suman@cse.uiu.ac.bd", "phone": "N/A", "expertise": "AI, ML"}
]
MOCK_JOBS = [
    {"title": "AI Internship at Grameenphone", "company": "Grameenphone", "location": "Remote", "link": "http://example.com"}
]
MOCK_TRENDING = [
    {"repo": "example/repo", "description": "A trending Python project", "language": "Python", "stars": "1.2k"}
]
MOCK_RESOURCES = [
    {"title": "Learn Python", "platform": "freeCodeCamp", "link": "https://freecodecamp.org/learn/python"}
]
MOCK_STACKOVERFLOW = [
    {"title": "How to debug Python code", "tags": "python, debugging", "link": "https://stackoverflow.com/questions/12345"}
]

# Web scraping with Trafilatura
async def fetch_web_content(url: str) -> str:
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as response:
                html = await response.text()
                return trafilatura.extract(html, include_links=True) or ""
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return ""

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
             InlineKeyboardButton("Help", callback_data='help')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            f"Welcome to the UIU Developer Hub Bot! 🎓\n"
            f"Use /help for commands. View your dashboard at http://localhost:{STREAMLIT_PORT}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Error starting the bot. Please try again.")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "📚 *UIU Developer Hub Bot Commands*\n\n"
            "1. **/start** - Start the bot and see welcome message.\n"
            "2. **/help** - Show this help message with all commands.\n"
            "3. **/calendar** - View UIU academic calendar events.\n"
            "4. **/resources [keyword]** - Find learning resources with Wikipedia summaries.\n"
            "   Example: /resources python\n"
            "5. **/trending [language]** - View GitHub trending repositories.\n"
            "   Example: /trending javascript\n"
            "6. **/stackoverflow [tag]** - View recent Stack Overflow questions.\n"
            "   Example: /stackoverflow python\n"
            "7. **/jobs [keyword]** - Find job and internship opportunities.\n"
            "   Example: /jobs internship\n"
            "8. **/roadmap <type> [level]** - Get a learning roadmap with Wikipedia context.\n"
            "   Example: /roadmap python beginner\n"
            "9. **/mentor find <department|expertise>** - Find UIU faculty mentors.\n"
            "   Example: /mentor find CSE\n"
            "10. **/notice** - View latest UIU notices.\n"
            "11. **/links** - Get university service links (UCAM, Library, etc.).\n"
            "12. **/cgpa <course:grade>** - Calculate CGPA.\n"
            "    Example: /cgpa cse321:A cse322:B+\n"
            "13. **/scholarships** - View scholarship opportunities.\n"
            "14. **/studyplan <courses> <hours> <date> <priority>** - Create a study plan.\n"
            "    Example: /studyplan cse321,cse322 10 2025-12-01 cse321:1,cse322:2\n"
            "15. **/reminders add <task> <date>** - Set reminders.\n"
            "    Example: /reminders add Meet Dr. Suman 2025-09-01\n"
            "16. **/reminders list** - List all reminders.\n"
            "17. **/motivate** - Get motivational tips.\n"
            "18. **/codeshare add <description> <tags> <code>** - Share code snippets.\n"
            "    Example: /codeshare add BubbleSort python def bubble_sort(arr):...\n"
            "19. **/codeshare list [tag]** - List your code snippets.\n"
            "    Example: /codeshare list python\n"
            "20. **/profile set <department> <year> <roadmaps>** - Set user profile.\n"
            "    Example: /profile set CSE 2 python,javascript\n"
            "21. **/progress [type]** - Track roadmap or study plan progress.\n"
            "    Example: /progress python\n"
            "22. **/recommend** - Get personalized resources, jobs, and mentors.\n"
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Error displaying help. Please try again.")

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
            # Parse events using Trafilatura's extracted text
            lines = content.split('\n')
            for line in lines[:5]:
                if any(kw in line.lower() for kw in ['event', 'seminar', 'holiday', 'exam']):
                    temp_calendar.append({"name": line.strip(), "date": "N/A", "details": line.strip()})

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
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting resources.")
            return

        global temp_resources
        temp_resources = []
        urls = [
            "https://www.freecodecamp.org/learn/",
            "https://www.coursera.org/courses?query=open%20source",
            "https://www.edx.org/search?q=open%20source"
        ]
        for url in urls:
            content = await fetch_web_content(url)
            if content:
                lines = content.split('\n')
                for line in lines[:5]:
                    if any(kw in line.lower() for kw in ['course', 'tutorial', 'learn']):
                        platform = url.split('/')[2].replace('www.', '')
                        temp_resources.append({"title": line.strip(), "platform": platform, "link": url})

        # Add Wikipedia summary if keyword provided
        wiki_summary = ""
        if keyword:
            page = wiki.page(keyword)
            if page.exists():
                wiki_summary = page.summary[:200] + "..." if len(page.summary) > 200 else page.summary

        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = c.fetchone()
        finally:
            conn.close()

        if temp_resources:
            response = f"Open-Source Learning Resources{f' for {keyword}' if keyword else ''}:\n"
            filtered_resources = temp_resources
            if keyword or (profile and profile[0]):
                filter_terms = [keyword] if keyword else profile[0].split(',')
                filtered_resources = [
                    r for r in temp_resources
                    if any(fuzz.partial_ratio(term.lower(), r['title'].lower()) > 70 for term in filter_terms)
                ]
            for resource in filtered_resources[:5]:
                response += f"- {resource['title']} ({resource['platform']}): {resource['link']}\n"
            if wiki_summary:
                response += f"\nWikipedia Summary for {keyword}:\n{wiki_summary}"
        else:
            response = "Unable to fetch resources. Using mock data:\n"
            for resource in MOCK_RESOURCES:
                response += f"- {resource['title']} ({resource['platform']}): {resource['link']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in resources command: {e}")
        await update.message.reply_text("Error fetching resources. Please try again.")

async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        language = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting trending repositories.")
            return

        global temp_trending
        temp_trending = []
        url = f"https://github.com/trending/{language}" if language else "https://github.com/trending"
        content = await fetch_web_content(url)
        if content:
            lines = content.split('\n')
            for line in lines[:5]:
                if 'stars' in line.lower() or 'repository' in line.lower():
                    temp_trending.append({
                        "repo": line.strip(), "description": "No description",
                        "language": language or "Unknown", "stars": "N/A"
                    })

        if temp_trending:
            response = f"GitHub Trending Repositories ({language or 'All'}):\n"
            for repo in temp_trending:
                response += f"- {repo['repo']} ({repo['language']}, {repo['stars']} stars): {repo['description']}\n"
        else:
            response = "Unable to fetch trending repositories. Using mock data:\n"
            for repo in MOCK_TRENDING:
                response += f"- {repo['repo']} ({repo['language']}, {repo['stars']} stars): {repo['description']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in trending command: {e}")
        await update.message.reply_text("Error fetching trending repos. Please try again.")

async def stackoverflow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        tag = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting Stack Overflow questions.")
            return

        global temp_stackoverflow
        temp_stackoverflow = []
        url = f"https://stackoverflow.com/questions/tagged/{tag}" if tag else "https://stackoverflow.com/questions"
        content = await fetch_web_content(url)
        if content:
            lines = content.split('\n')
            for line in lines[:5]:
                if any(kw in line.lower() for kw in ['question', 'tagged']):
                    temp_stackoverflow.append({
                        "title": line.strip(), "tags": tag or "general",
                        "link": f"https://stackoverflow.com/questions"
                    })

        if temp_stackoverflow:
            response = f"Stack Overflow Questions ({tag or 'Recent'}):\n"
            for question in temp_stackoverflow:
                response += f"- {question['title']} (Tags: {question['tags']}): {question['link']}\n"
        else:
            response = "Unable to fetch Stack Overflow questions. Using mock data:\n"
            for question in MOCK_STACKOVERFLOW:
                response += f"- {question['title']} (Tags: {question['tags']}): {question['link']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in stackoverflow command: {e}")
        await update.message.reply_text("Error fetching Stack Overflow questions. Please try again.")

async def jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        keyword = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting jobs.")
            return

        global temp_jobs
        temp_jobs = []
        urls = [
            "https://www.internshala.com/internships",
            "https://www.bdjobs.com/",
            "https://www.linkedin.com/jobs/search/?keywords=internship&location=Bangladesh",
            "https://weworkremotely.com/"
        ]
        for url in urls:
            content = await fetch_web_content(url)
            if content:
                lines = content.split('\n')
                for line in lines[:5]:
                    if any(kw in line.lower() for kw in ['job', 'internship', 'career']):
                        company = url.split('/')[2].replace('www.', '')
                        temp_jobs.append({
                            "title": line.strip(), "company": company,
                            "location": "N/A", "link": url
                        })

        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT department, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = c.fetchone()
        finally:
            conn.close()

        if temp_jobs:
            response = "Job & Internship Opportunities:\n"
            filtered_jobs = temp_jobs
            if keyword or (profile and profile[1]):
                filter_terms = [keyword, "bangladesh", "uiu"] if keyword else profile[1].split(',') + ["bangladesh", "uiu"]
                filtered_jobs = [
                    j for j in temp_jobs
                    if any(fuzz.partial_ratio(term.lower(), j['title'].lower()) > 70 for term in filter_terms)
                ]
            for job in filtered_jobs[:5]:
                response += f"- {job['title']} at {job['company']} ({job['location']}): {job['link']}\n"
            keyboard = [[InlineKeyboardButton("Add Job Reminder", callback_data='add_reminder_job')]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            response = "Unable to fetch jobs. Using mock data:\n"
            for job in MOCK_JOBS:
                response += f"- {job['title']} at {job['company']} ({job['location']}): {job['link']}\n"
            reply_markup = None
        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in jobs command: {e}")
        await update.message.reply_text("Error fetching jobs. Please try again.")

async def roadmap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args:
            await update.message.reply_text("Please specify a roadmap, e.g., /roadmap python [beginner|intermediate|advanced]")
            return
        roadmap_type = args[0].lower()
        level = args[1].lower() if len(args) > 1 and args[1].lower() in ['beginner', 'intermediate', 'advanced'] else None

        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting another roadmap.")
            return

        # Fetch roadmap from roadmap.sh
        content = await fetch_web_content(f"https://roadmap.sh/{roadmap_type}")
        steps, resources, projects = [], [], []
        if content:
            lines = content.split('\n')
            for line in lines:
                if any(kw in line.lower() for kw in ['step', 'task']):
                    steps.append(line.strip())
                elif any(kw in line.lower() for kw in ['resource', 'tutorial', 'course']):
                    resources.append(line.strip())
                elif any(kw in line.lower() for kw in ['project', 'example']):
                    projects.append(line.strip())

        # Add Wikipedia summary
        wiki_summary = ""
        page = wiki.page(roadmap_type)
        if page.exists():
            wiki_summary = page.summary[:200] + "..." if len(page.summary) > 200 else page.summary

        # Save to database
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            title = f"{level.capitalize() if level else 'General'} {roadmap_type.capitalize()} Roadmap"
            c.execute(
                "INSERT OR REPLACE INTO roadmaps (roadmap_type, level, title, steps, resources, projects, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (roadmap_type, level or 'general', title, json.dumps(steps or ["Step not found"]),
                 json.dumps(resources or ["Resource not found"]), json.dumps(projects or ["Project not found"]), time.time())
            )
            conn.commit()
            c.execute("SELECT title, steps, resources, projects FROM roadmaps WHERE roadmap_type = ? AND level = ?",
                      (roadmap_type, level or 'general'))
            roadmap = c.fetchone()
        finally:
            conn.close()

        if roadmap:
            title, steps, resources, projects = roadmap
            steps = json.loads(steps)
            resources = json.loads(resources)
            projects = json.loads(projects)
            response = f"{title}:\n\nSteps:\n" + "\n".join(f"- {step}" for step in steps) + \
                       f"\n\nResources:\n" + "\n".join(f"- {res}" for res in resources) + \
                       f"\n\nProjects:\n" + "\n".join(f"- {proj}" for proj in projects)
            if wiki_summary:
                response += f"\n\nWikipedia Summary for {roadmap_type}:\n{wiki_summary}"
            keyboard = [
                [InlineKeyboardButton("Beginner", callback_data=f'roadmap_{roadmap_type}_beginner'),
                 InlineKeyboardButton("Intermediate", callback_data=f'roadmap_{roadmap_type}_intermediate'),
                 InlineKeyboardButton("Advanced", callback_data=f'roadmap_{roadmap_type}_advanced')]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            response = f"Unable to fetch {roadmap_type} roadmap. Using mock data:\n"
            roadmap = MOCK_ROADMAP
            response += f"{roadmap['title']}:\n\nSteps:\n" + "\n".join(f"- {step}" for step in roadmap['steps']) + \
                        f"\n\nResources:\n" + "\n".join(f"- {res}" for res in roadmap['resources']) + \
                        f"\n\nProjects:\n" + "\n".join(f"- {proj}" for proj in roadmap['projects'])
            reply_markup = None

        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in roadmap command: {e}")
        await update.message.reply_text("Error fetching roadmap. Please try again.")

async def mentor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args or args[0].lower() != "find" or len(args) < 2:
            await update.message.reply_text("Usage: /mentor find [department|expertise]\nExample: /mentor find CSE")
            return
        query = args[1].lower()

        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting another mentor search.")
            return

        global temp_faculty
        temp_faculty = []
        urls = [
            "https://cse.uiu.ac.bd/faculty-members/",
            "https://eee.uiu.ac.bd/faculty/",
            "https://ce.uiu.ac.bd/faculty-members/",
            "https://sobe.uiu.ac.bd/bba-faculty/",
            "https://pharmacy.uiu.ac.bd/faculty-members/",
            "https://ins.uiu.ac.bd/faculty-members/",
            "https://www.uiu.ac.bd/faculty-members/"
        ]
        for url in urls:
            content = await fetch_web_content(url)
            if content:
                lines = content.split('\n')
                for line in lines:
                    if any(kw in line.lower() for kw in ['professor', 'lecturer', 'dr.']):
                        department = url.split('/')[2].replace('www.', '').split('.')[0].upper()
                        temp_faculty.append({
                            "name": line.strip(), "designation": "N/A", "department": department,
                            "email": "N/A", "phone": "N/A", "expertise": query
                        })

        if temp_faculty:
            response = f"Mentors for '{query}':\n"
            filtered_faculty = [
                f for f in temp_faculty
                if fuzz.partial_ratio(query.lower(), f['department'].lower()) > 70 or
                   fuzz.partial_ratio(query.lower(), f['expertise'].lower()) > 70
            ]
            if filtered_faculty:
                for f in filtered_faculty:
                    response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
                keyboard = [[InlineKeyboardButton(f"Contact {f['name']}", callback_data=f'contact_{f["name"].replace(" ", "_")}')]
                           for f in filtered_faculty[:5]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                response = f"No mentors found for '{query}'. Using mock data:\n"
                for f in MOCK_FACULTY:
                    if fuzz.partial_ratio(query.lower(), f['department'].lower()) > 70 or fuzz.partial_ratio(query.lower(), f['expertise'].lower()) > 70:
                        response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
                reply_markup = None
        else:
            response = f"No mentors found for '{query}'. Using mock data:\n"
            for f in MOCK_FACULTY:
                if fuzz.partial_ratio(query.lower(), f['department'].lower()) > 70 or fuzz.partial_ratio(query.lower(), f['expertise'].lower()) > 70:
                    response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
            reply_markup = None

        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in mentor command: {e}")
        await update.message.reply_text("Error fetching mentors. Please try again.")

async def notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait 30 seconds before requesting notices.")
            return

        global temp_notices
        temp_notices = []
        content = await fetch_web_content("https://www.uiu.ac.bd")
        if content:
            lines = content.split('\n')
            for i, line in enumerate(lines[:3], 1):
                if any(kw in line.lower() for kw in ['notice', 'announcement', 'update']):
                    temp_notices.append({"title": line.strip(), "date": "N/A", "details": line.strip()})

        if temp_notices:
            response = "Latest UIU Notices:\n"
            for i, notice in enumerate(temp_notices, 1):
                response += f"{i}. {notice['title']} ({notice['date']}): {notice['details']}\n"
        else:
            response = "Unable to fetch notices. Using mock data:\n"
            for notice in MOCK_NOTICES:
                response += f"- {notice['title']} ({notice['date']}): {notice['details']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in notice command: {e}")
        await update.message.reply_text("Error fetching notices. Please try again.")

async def links(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = (
            "University Services Shortcuts:\n"
            "- UCAM: http://ucam.uiu.ac.bd\n"
            "- UIU Library: http://www.uiu.ac.bd/library\n"
            "- Student Portal: http://www.uiu.ac.bd/student-portal\n"
            "- Developer Hub GitHub: https://github.com/UIU-Developer-Hub\n"
        )
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in links command: {e}")
        await update.message.reply_text("Error fetching links. Please try again.")

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

async def scholarships(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = "Scholarship Opportunities for UIU Students:\n"
        for scholarship in MOCK_SCHOLARSHIPS:
            response += f"- {scholarship['name']}: {scholarship['details']} ({scholarship['link']})\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in scholarships command: {e}")
        await update.message.reply_text("Error fetching scholarships. Please try again.")

async def studyplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 4:
            await update.message.reply_text("Usage: /studyplan course1,course2 hours_per_week target_date(YYYY-MM-DD) priority(course1:1,cse322:2)")
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
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("INSERT INTO progress (user_id, roadmap_type, level, completed_steps) VALUES (?, ?, ?, ?)",
                      (update.effective_user.id, 'studyplan', 'current', json.dumps([])))
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
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
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
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT favorite_roadmaps, department FROM user_profiles WHERE user_id = ?", (update.effective_user.id,))
            profile = c.fetchone()
        finally:
            conn.close()
        tips = [
            "Keep coding! Every line you write brings you closer to mastery.",
            "Stay curious and keep learning. You've got this!",
            "Break down complex problems into small steps. You can do it!"
        ]
        if profile and profile[0]:
            tips.append(f"Keep pushing on your {profile[0].split(',')[0]} roadmap! Small steps lead to big wins.")
        if profile and profile[1]:
            tips.append(f"Stay focused on your {profile[1]} studies. You're building a bright future!")
        await update.message.reply_text(random.choice(tips))
    except Exception as e:
        logger.error(f"Error in motivate command: {e}")
        await update.message.reply_text("Error fetching motivational tip. Please try again.")

async def codeshare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /codeshare add 'description' 'tags' 'code' or /codeshare list [tag]")
            return
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            if args[0].lower() == "add":
                if len(args) < 3:
                    await update.message.reply_text("Usage: /codeshare add 'description' 'tags' 'code'")
                    return
                description, tags, code = args[1], args[2], " ".join(args[3:])
                c.execute("INSERT INTO code_snippets (user_id, description, tags, snippet) VALUES (?, ?, ?, ?)",
                          (user_id, description, tags, code))
                conn.commit()
                await update.message.reply_text(f"Code snippet saved: {description} (Tags: {tags})")
            else:
                tag = args[1] if len(args) > 1 else None
                query = "SELECT description, tags, snippet FROM code_snippets WHERE user_id = ?"
                params = [user_id]
                if tag:
                    query += " AND tags LIKE ?"
                    params.append(f"%{tag}%")
                c.execute(query, params)
                snippets = c.fetchall()
                response = "Your Code Snippets:\n" + "\n".join(f"- {desc} (Tags: {tags}): {code}" for desc, tags, code in snippets)
                await update.message.reply_text(response or "No snippets found.")
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"Error in codeshare command: {e}")
        await update.message.reply_text("Error handling code snippets. Please try again.")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args or args[0].lower() != "set" or len(args) < 4:
            await update.message.reply_text("Usage: /profile set department year favorite_roadmaps\nExample: /profile set CSE 2 python,javascript")
            return
        department, year, favorite_roadmaps = args[1], int(args[2]), args[3]
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO user_profiles (user_id, department, year, favorite_roadmaps) VALUES (?, ?, ?, ?)",
                      (user_id, department, year, favorite_roadmaps))
            conn.commit()
        finally:
            conn.close()
        await update.message.reply_text(f"Profile updated: {department}, Year {year}, Roadmaps: {favorite_roadmaps}")
    except Exception as e:
        logger.error(f"Error in profile command: {e}")
        await update.message.reply_text("Error setting profile. Please try again.")

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        roadmap_type = args[0].lower() if args else 'studyplan'
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT roadmap_type, level, completed_steps FROM progress WHERE user_id = ? AND roadmap_type = ?",
                      (user_id, roadmap_type))
            progress = c.fetchall()
        finally:
            conn.close()
        if progress:
            response = f"Progress on {roadmap_type}:\n"
            for rt, level, steps in progress:
                steps = json.loads(steps)
                response += f"- {level}: {len(steps)} steps completed\n"
            await update.message.reply_text(response)
        else:
            await update.message.reply_text(f"No progress tracked for {roadmap_type}. Start with /studyplan or /roadmap.")
    except Exception as e:
        logger.error(f"Error in progress command: {e}")
        await update.message.reply_text("Error fetching progress. Please try again.")

async def recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        try:
            c = conn.cursor()
            c.execute("SELECT department, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = c.fetchone()
        finally:
            conn.close()
        response = "Recommended Resources & Opportunities:\n"
        if profile and profile[1]:
            roadmaps, dept = profile[1], profile[0]
            response += f"For {roadmaps.split(',')[0]} and {dept}:\n"
            if temp_resources:
                filtered_resources = [
                    r for r in temp_resources
                    if any(fuzz.partial_ratio(term.lower(), r['title'].lower()) > 70 for term in roadmaps.split(','))
                ]
                for resource in filtered_resources[:3]:
                    response += f"- Resource: {resource['title']} ({resource['platform']}): {resource['link']}\n"
            if temp_trending:
                filtered_trending = [
                    r for r in temp_trending
                    if any(fuzz.partial_ratio(term.lower(), r['language'].lower()) > 70 for term in roadmaps.split(','))
                ]
                for repo in filtered_trending[:3]:
                    response += f"- Repo: {repo['repo']} ({repo['language']}): {repo['description']}\n"
            if temp_jobs:
                filtered_jobs = [
                    j for j in temp_jobs
                    if any(fuzz.partial_ratio(term.lower(), j['title'].lower()) > 70 for term in roadmaps.split(','))
                ]
                for job in filtered_jobs[:3]:
                    response += f"- Job: {job['title']} at {job['company']} ({job['location']}): {job['link']}\n"
            if temp_faculty:
                filtered_faculty = [
                    f for f in temp_faculty
                    if fuzz.partial_ratio(dept.lower(), f['department'].lower()) > 70 or
                       any(fuzz.partial_ratio(term.lower(), f['expertise'].lower()) > 70 for term in roadmaps.split(','))
                ]
                if filtered_faculty:
                    response += "Suggested Mentors:\n" + "\n".join(f"- {f['name']} ({f['email']})" for f in filtered_faculty[:3])
            else:
                response += "Suggested Mentors:\n" + "\n".join(
                    f"- {f['name']} ({f['email']})" for f in MOCK_FACULTY
                    if fuzz.partial_ratio(dept.lower(), f['department'].lower()) > 70 or
                       any(fuzz.partial_ratio(term.lower(), f['expertise'].lower()) > 70 for term in roadmaps.split(','))
                )
        else:
            response += "Set your profile with /profile to get personalized recommendations."
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in recommend command: {e}")
        await update.message.reply_text("Error fetching recommendations. Please try again.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        if query.data.startswith('roadmap_'):
            _, roadmap_type, level = query.data.split('_')
            conn = sqlite3.connect('uiu_bot.db', timeout=10)
            try:
                c = conn.cursor()
                c.execute("SELECT title, steps, resources, projects FROM roadmaps WHERE roadmap_type = ? AND level = ?",
                          (roadmap_type, level))
                roadmap = c.fetchone()
            finally:
                conn.close()
            if roadmap:
                title, steps, resources, projects = roadmap
                steps = json.loads(steps)
                resources = json.loads(resources)
                projects = json.loads(projects)
                response = f"{title}:\n\nSteps:\n" + "\n".join(f"- {step}" for step in steps) + \
                           f"\n\nResources:\n" + "\n".join(f"- {res}" for res in resources) + \
                           f"\n\nProjects:\n" + "\n".join(f"- {proj}" for proj in projects)
            else:
                response = f"No {level} roadmap found for {roadmap_type}. Try /roadmap {roadmap_type} {level}"
            await query.message.reply_text(response)
        elif query.data.startswith('contact_'):
            name = query.data.replace('contact_', '').replace('_', ' ')
            filtered_faculty = [f for f in temp_faculty if f['name'] == name]
            if filtered_faculty:
                f = filtered_faculty[0]
                await query.message.reply_text(f"Contact {name}:\nEmail: {f['email']}\nPhone: {f['phone']}")
            else:
                for f in MOCK_FACULTY:
                    if f['name'] == name:
                        await query.message.reply_text(f"Contact {name}:\nEmail: {f['email']}\nPhone: {f['phone']}")
                        return
                await query.message.reply_text(f"Contact info for {name} not found.")
        elif query.data == 'view_profile':
            user_id = query.from_user.id
            conn = sqlite3.connect('uiu_bot.db', timeout=10)
            try:
                c = conn.cursor()
                c.execute("SELECT department, year, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
                profile = c.fetchone()
            finally:
                conn.close()
            if profile:
                dept, year, roadmaps = profile
                await query.message.reply_text(f"Profile:\nDepartment: {dept}\nYear: {year}\nFavorite Roadmaps: {roadmaps}")
            else:
                await query.message.reply_text("No profile set. Use /profile set dept year roadmaps")
        elif query.data == 'set_profile':
            await query.message.reply_text("Set your profile with /profile set department year favorite_roadmaps\nExample: /profile set CSE 2 python,javascript")
        elif query.data == 'add_reminder_calendar':
            await query.message.reply_text("To add a calendar event reminder, use /reminders add 'Event Name' YYYY-MM-DD")
        elif query.data == 'add_reminder_job':
            await query.message.reply_text("To add a job application reminder, use /reminders add 'Apply for Job Title' YYYY-MM-DD")
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
    st.set_page_config(page_title="UIU Developer Hub Dashboard", page_icon="🎓")
    st.title("UIU Developer Hub Dashboard")
    st.header("User Profile and Progress")

    conn = sqlite3.connect('uiu_bot.db', timeout=10)
    try:
        # User Profiles
        profiles = pl.read_database("SELECT * FROM user_profiles", conn)
        if not profiles.is_empty():
            st.subheader("User Profiles")
            st.dataframe(profiles)
        else:
            st.write("No user profiles found.")

        # Study Plans (Progress)
        progress = pl.read_database("SELECT * FROM progress", conn)
        if not progress.is_empty():
            st.subheader("Study Plan Progress")
            st.dataframe(progress)
        else:
            st.write("No study plan progress found.")

        # Reminders
        reminders = pl.read_database("SELECT * FROM reminders", conn)
        if not reminders.is_empty():
            st.subheader("Reminders")
            st.dataframe(reminders)
        else:
            st.write("No reminders set.")

        # Code Snippets
        snippets = pl.read_database("SELECT * FROM code_snippets", conn)
        if not snippets.is_empty():
            st.subheader("Code Snippets")
            st.dataframe(snippets)
        else:
            st.write("No code snippets found.")
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
    return web.Response(text="UIU Student Bot is running", status=200)

# Setup Telegram application and webhook
async def setup_application():
    global application
    try:
        if not BOT_TOKEN:
            logger.error("BOT_TOKEN environment variable not set")
            raise ValueError("BOT_TOKEN not set")

        application = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("help", help))
        application.add_handler(CommandHandler("calendar", calendar))
        application.add_handler(CommandHandler("resources", resources))
        application.add_handler(CommandHandler("trending", trending))
        application.add_handler(CommandHandler("stackoverflow", stackoverflow))
        application.add_handler(CommandHandler("jobs", jobs))
        application.add_handler(CommandHandler("roadmap", roadmap))
        application.add_handler(CommandHandler("mentor", mentor))
        application.add_handler(CommandHandler("notice", notice))
        application.add_handler(CommandHandler("links", links))
        application.add_handler(CommandHandler("cgpa", cgpa))
        application.add_handler(CommandHandler("scholarships", scholarships))
        application.add_handler(CommandHandler("studyplan", studyplan))
        application.add_handler(CommandHandler("reminders", reminders))
        application.add_handler(CommandHandler("motivate", motivate))
        application.add_handler(CommandHandler("codeshare", codeshare))
        application.add_handler(CommandHandler("profile", profile))
        application.add_handler(CommandHandler("progress", progress))
        application.add_handler(CommandHandler("recommend", recommend))
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

# Main function to run both Telegram bot and Streamlit
async def main():
    try:
        # Create aiohttp app for Telegram webhook
        app = web.Application()
        app.router.add_post('/webhook', webhook_handler)
        app.router.add_get('/', health_check)

        # Setup Telegram application
        await setup_application()

        # Start webhook server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', PORT)
        await site.start()
        logger.info(f"Webhook server running on port {PORT}")

        # Start Streamlit in a separate process
        import subprocess
        subprocess.Popen(["streamlit", "run", __file__, "--server.port", str(STREAMLIT_PORT)])

        # Keep the application running
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
