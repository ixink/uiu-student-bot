import logging
import sqlite3
import pandas as pd
import random
import requests
import os
import json
import time
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
import scrapy
from scrapy.crawler import CrawlerProcess
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import BaseModel, ValidationError
from aiohttp import web
from typing import List, Dict
from requests.exceptions import HTTPError, ConnectionError, Timeout

# Configure logging
logging.config.fileConfig('logging.conf')
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://uiu-student-bot.onrender.com/webhook")
PORT = int(os.getenv("PORT", 8443))

# Initialize SQLite database with retry logic
def init_db():
    retries = 3
    for attempt in range(retries):
        try:
            conn = sqlite3.connect('uiu_bot.db', timeout=10)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS reminders (user_id INTEGER, task TEXT, deadline TEXT, recurrence TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS code_snippets (user_id INTEGER, description TEXT, tags TEXT, snippet TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS roadmaps (roadmap_type TEXT, level TEXT, title TEXT, steps TEXT, resources TEXT, projects TEXT, last_updated REAL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS user_profiles (user_id INTEGER PRIMARY KEY, department TEXT, year INTEGER, favorite_roadmaps TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS progress (user_id INTEGER, roadmap_type TEXT, level TEXT, completed_steps TEXT)''')
            c.execute('''CREATE TABLE IF NOT EXISTS faculty (name TEXT, designation TEXT, department TEXT, email TEXT, phone TEXT, expertise TEXT, last_updated REAL)''')
            conn.commit()
            conn.close()
            logger.info("Database initialized successfully")
            return
        except sqlite3.OperationalError as e:
            logger.error(f"Database init attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                time.sleep(1)
            else:
                raise

init_db()

# Temporary in-memory storage
temp_calendar: List[Dict] = []
temp_resources: List[Dict] = []
temp_trending: List[Dict] = []
temp_stackoverflow: List[Dict] = []
temp_jobs: List[Dict] = []
temp_notices: List[Dict] = []
temp_faculty: List[Dict] = []
temp_faculty_urls = set()

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
    {"name": "Dr. Suman Ahmmed", "designation": "Head", "department": "CSE", "email": "suman@cse.uiu.ac.bd", "phone": "N/A", "expertise": "AI, ML"},
    {"name": "Dr. Rumana Afrin", "designation": "Head", "department": "Civil Engineering", "email": "rumana@ce.uiu.ac.bd", "phone": "N/A", "expertise": "Structural Engineering"},
    {"name": "Dr. Mohammad Musa", "designation": "Dean", "department": "Business Administration", "email": "musa@sobe.uiu.ac.bd", "phone": "N/A", "expertise": "Finance"},
    {"name": "Dr. Mohammad Omar Farooq", "designation": "Head", "department": "Economics", "email": "farooq@sobe.uiu.ac.bd", "phone": "N/A", "expertise": "Economic Policy"}
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

# Scrapy Spiders (unchanged from previous)
class RoadmapSpider(scrapy.Spider):
    name = "roadmap"
    def __init__(self, roadmap_type="python", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.start_urls = [f"https://roadmap.sh/{roadmap_type}"]

    def parse(self, response):
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        roadmap_type = response.url.split('/')[-1]
        levels = ['beginner', 'intermediate', 'advanced']
        for level in levels:
            section = response.css(f'div[data-level="{level}"]') or response.css('div.roadmap-section')
            title = section.css('h2::text').get(default=f"{level.capitalize()} {roadmap_type.capitalize()} Roadmap")
            steps = section.css('ul.steps li::text').getall() or ["Step not found"]
            resources = section.css('ul.resources li a::text').getall() or ["Resource not found"]
            projects = section.css('ul.projects li::text').getall() or ["Project not found"]
            c.execute(
                "INSERT OR REPLACE INTO roadmaps (roadmap_type, level, title, steps, resources, projects, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (roadmap_type, level, title, json.dumps(steps), json.dumps(resources), json.dumps(projects), time.time())
            )
        conn.commit()
        conn.close()

class FacultySpider(scrapy.Spider):
    name = "faculty"
    start_urls = [
        "https://cse.uiu.ac.bd/faculty-members/",
        "https://eee.uiu.ac.bd/faculty/",
        "https://ce.uiu.ac.bd/faculty-members/",
        "https://sobe.uiu.ac.bd/bba-faculty/",
        "https://pharmacy.uiu.ac.bd/faculty-members/",
        "https://ins.uiu.ac.bd/faculty-members/",
        "https://www.uiu.ac.bd/faculty-members/"
    ]

    def parse(self, response):
        global temp_faculty, temp_faculty_urls
        if response.url not in temp_faculty_urls:
            temp_faculty_urls.add(response.url)
            try:
                faculty_list = response.css('div.faculty-member, tr.member, div.staff-card, li.faculty')
                for faculty in faculty_list:
                    name = faculty.css('h3::text, td.name::text, .faculty-name::text').get(default="Unknown").strip()
                    designation = faculty.css('p.designation::text, td.designation::text, .faculty-title::text').get(default="N/A").strip()
                    department = self.get_department(response.url)
                    email = faculty.css('a.email::text, td.email::text, .faculty-email::text').get(default="N/A").strip()
                    phone = faculty.css('span.phone::text, td.phone::text, .faculty-phone::text').get(default="N/A").strip()
                    expertise = faculty.css('p.expertise::text, td.expertise::text, .faculty-expertise::text').get(default="N/A").strip()
                    temp_faculty.append({
                        "name": name, "designation": designation, "department": department,
                        "email": email, "phone": phone, "expertise": expertise
                    })
            except Exception as e:
                logger.error(f"Error parsing faculty data from {response.url}: {e}")

    def get_department(self, url):
        if "cse.uiu.ac.bd" in url:
            return "Computer Science and Engineering"
        elif "eee.uiu.ac.bd" in url:
            return "Electrical and Electronic Engineering"
        elif "ce.uiu.ac.bd" in url:
            return "Civil Engineering"
        elif "sobe.uiu.ac.bd" in url:
            return "Business Administration"
        elif "pharmacy.uiu.ac.bd" in url:
            return "Pharmacy"
        elif "ins.uiu.ac.bd" in url:
            return "Institute of Natural Sciences"
        else:
            return "Economics, English, EDS, MSJ, or BGE"

class CalendarSpider(scrapy.Spider):
    name = "calendar"
    start_urls = ["https://www.uiu.ac.bd/academic-calendars"]

    def parse(self, response):
        global temp_calendar
        temp_calendar = []
        try:
            events = response.css('div.event-item, li.event')[:5]
            for event in events:
                name = event.css('h3::text, .event-title::text').get(default="No title")
                date = event.css('time::text, .event-date::text').get(default="No date")
                details = event.css('p::text, .event-details::text').get(default="No details")
                temp_calendar.append({"name": name, "date": date, "details": details})
        except Exception as e:
            logger.error(f"Error parsing calendar data: {e}")

class ResourcesSpider(scrapy.Spider):
    name = "resources"
    start_urls = [
        "https://www.freecodecamp.org/learn/",
        "https://www.coursera.org/courses?query=open%20source",
        "https://www.edx.org/search?q=open%20source"
    ]

    def parse(self, response):
        global temp_resources
        temp_resources = []
        try:
            courses = response.css('div.course-item, li.course')[:5]
            for course in courses:
                title = course.css('h2::text, .course-title::text').get(default="No title")
                platform = response.url.split('/')[2].replace('www.', '')
                link = course.css('a::attr(href)').get(default="#")
                if not link.startswith('http'):
                    link = f"https://{platform}{link}"
                temp_resources.append({"title": title, "platform": platform, "link": link})
        except Exception as e:
            logger.error(f"Error parsing resources: {e}")

class TrendingSpider(scrapy.Spider):
    name = "trending"
    def __init__(self, language=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        url = "https://github.com/trending" if not language else f"https://github.com/trending/{language}"
        self.start_urls = [url]

    def parse(self, response):
        global temp_trending
        temp_trending = []
        try:
            repos = response.css('article.Box-row')[:5]
            for repo in repos:
                repo_name = repo.css('h1 a::text').get(default="No name").strip()
                description = repo.css('p::text').get(default="No description").strip()
                language = repo.css('span[itemprop="programmingLanguage"]::text').get(default="Unknown")
                stars = repo.css('a[href*="/stargazers"]::text').get(default="0").strip()
                temp_trending.append({"repo": repo_name, "description": description, "language": language, "stars": stars})
        except Exception as e:
            logger.error(f"Error parsing trending repos: {e}")

class StackOverflowSpider(scrapy.Spider):
    name = "stackoverflow"
    def __init__(self, tag=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        url = "https://stackoverflow.com/questions" if not tag else f"https://stackoverflow.com/questions/tagged/{tag}"
        self.start_urls = [url]

    def parse(self, response):
        global temp_stackoverflow
        temp_stackoverflow = []
        try:
            questions = response.css('div.question-summary')[:5]
            for question in questions:
                title = question.css('h3 a::text').get(default="No title")
                tags = question.css('div.tags a::text').getall()
                link = question.css('h3 a::attr(href)').get(default="#")
                temp_stackoverflow.append({"title": title, "tags": ",".join(tags), "link": f"https://stackoverflow.com{link}"})
        except Exception as e:
            logger.error(f"Error parsing Stack Overflow: {e}")

class JobsSpider(scrapy.Spider):
    name = "jobs"
    start_urls = [
        "https://www.internshala.com/internships",
        "https://www.bdjobs.com/",
        "https://www.linkedin.com/jobs/search/?keywords=internship&location=Bangladesh",
        "https://weworkremotely.com/"
    ]

    def parse(self, response):
        global temp_jobs
        temp_jobs = []
        try:
            if "internshala" in response.url:
                jobs = response.css('div.internship_meta')[:5]
                for job in jobs:
                    title = job.css('h3::text').get(default="No title").strip()
                    company = job.css('a.company_name::text').get(default="Unknown").strip()
                    location = job.css('div.location::text').get(default="Remote").strip()
                    link = job.css('a.view_detail_button::attr(href)').get(default="#")
                    temp_jobs.append({"title": title, "company": company, "location": location, "link": f"https://www.internshala.com{link}"})
            elif "bdjobs" in response.url:
                jobs = response.css('div.job-list-item')[:5]
                for job in jobs:
                    title = job.css('h2::text').get(default="No title").strip()
                    company = job.css('span.company::text').get(default="Unknown").strip()
                    location = job.css('span.location::text').get(default="Unknown").strip()
                    link = job.css('a::attr(href)').get(default="#")
                    temp_jobs.append({"title": title, "company": company, "location": location, "link": link})
            elif "linkedin" in response.url:
                jobs = response.css('div.job-card')[:5]
                for job in jobs:
                    title = job.css('h3::text').get(default="No title").strip()
                    company = job.css('h4::text').get(default="Unknown").strip()
                    location = job.css('span.job-location::text').get(default="Remote").strip()
                    link = job.css('a::attr(href)').get(default="#")
                    temp_jobs.append({"title": title, "company": company, "location": location, "link": link})
            elif "weworkremotely" in response.url:
                jobs = response.css('li.feature')[:5]
                for job in jobs:
                    title = job.css('span.title::text').get(default="No title").strip()
                    company = job.css('span.company::text').get(default="Unknown").strip()
                    location = "Remote"
                    link = job.css('a::attr(href)').get(default="#")
                    temp_jobs.append({"title": title, "company": company, "location": location, "link": f"https://weworkremotely.com{link}"})
        except Exception as e:
            logger.error(f"Error parsing jobs: {e}")

class NoticeSpider(scrapy.Spider):
    name = "uiu_notice"
    start_urls = ["https://www.uiu.ac.bd"]

    def parse(self, response):
        global temp_notices
        temp_notices = []
        try:
            notices = response.css('div.news-section article')[:3]
            for notice in notices:
                title = notice.css('h2::text').get() or "No title"
                date = notice.css('time::text').get() or "No date"
                details = notice.css('p::text').get() or "No details"
                temp_notices.append({"title": title, "date": date, "details": details})
        except Exception as e:
            logger.error(f"Error parsing notices: {e}")

# Selenium Scraper for dynamic content
def scrape_dynamic_content(url, retries=2):
    for attempt in range(retries):
        try:
            options = webdriver.ChromeOptions()
            options.add_argument('--headless')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            driver.get(url)
            time.sleep(2)
            content = driver.page_source
            driver.quit()
            return content
        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed for {url}: {e}")
            time.sleep(1)
    return None

# Pydantic models
class StudyPlan(BaseModel):
    courses: list[str]
    hours_per_week: int
    target_date: str
    priority: str

class UserProfile(BaseModel):
    department: str
    year: int
    favorite_roadmaps: str

# Rate limiting
user_last_scrape = {}

def can_scrape(user_id):
    last_scrape = user_last_scrape.get(user_id, 0)
    if time.time() - last_scrape < 60:
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
            "Welcome to the UIU Developer Hub Bot! 🎓\n"
            "Use /help for a list of commands and usage instructions.",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        await update.message.reply_text("Error starting the bot. Please try again or use /help for details.")

async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        help_text = (
            "📚 *UIU Developer Hub Bot Commands*\n\n"
            "1. **/start** - Start the bot and see welcome message.\n"
            "2. **/help** - Show this help message with all commands.\n"
            "3. **/calendar** - View UIU academic calendar events.\n"
            "4. **/resources [keyword]** - Find open-source learning resources.\n"
            "   Example: /resources python\n"
            "5. **/trending [language]** - View GitHub trending repositories.\n"
            "   Example: /trending javascript\n"
            "6. **/stackoverflow [tag]** - View recent Stack Overflow questions.\n"
            "   Example: /stackoverflow python\n"
            "7. **/jobs [keyword]** - Find job and internship opportunities.\n"
            "   Example: /jobs internship\n"
            "8. **/roadmap <type> [level]** - Get a learning roadmap from roadmap.sh.\n"
            "   Example: /roadmap python beginner\n"
            "9. **/mentor find <department|expertise>** - Find UIU faculty mentors from all departments (CSE, EEE, Civil Engineering, Business Administration, Economics, Pharmacy, INS, English, EDS, MSJ, BGE).\n"
            "   Example: /mentor find CSE\n"
            "10. **/notice** - View latest UIU notices.\n"
            "11. **/collab post <message>** - Post a collaboration request.\n"
            "    Example: /collab post Looking for hackathon teammates\n"
            "12. **/events** - View upcoming UIU events.\n"
            "13. **/links** - Get university service links (UCAM, Library, etc.).\n"
            "14. **/leaderboard** - View developer recognition leaderboard (coming soon).\n"
            "15. **/meetup** - View tech meetups and coding jams (coming soon).\n"
            "16. **/internship** - Find internships (use /jobs).\n"
            "17. **/cgpa <course:grade>** - Calculate CGPA.\n"
            "    Example: /cgpa cse321:A cse322:B+\n"
            "18. **/gpapredict <current_cgpa> <target_cgpa>** - Predict grades needed.\n"
            "    Example: /gpapredict 3.5 3.8\n"
            "19. **/scholarships** - View scholarship opportunities.\n"
            "20. **/academic** - View academic calendar (same as /calendar).\n"
            "21. **/career** - Career office updates (coming soon).\n"
            "22. **/fyp <ideas|docs>** - Get FYP ideas or documentation (coming soon).\n"
            "    Example: /fyp ideas\n"
            "23. **/studyplan <courses> <hours> <date> <priority>** - Create a study plan.\n"
            "    Example: /studyplan cse321,cse322 10 2025-12-01 cse321:1,cse322:2\n"
            "24. **/reminders add <task> <date> [recurrence]** - Set reminders.\n"
            "    Example: /reminders add Meet Dr. Suman 2025-09-01 weekly\n"
            "25. **/reminders list** - List all reminders.\n"
            "26. **/motivate** - Get motivational tips.\n"
            "27. **/codeshare add <description> <tags> <code>** - Share code snippets.\n"
            "    Example: /codeshare add BubbleSort python def bubble_sort(arr):...\n"
            "28. **/codeshare list [tag]** - List your code snippets.\n"
            "    Example: /codeshare list python\n"
            "29. **/profile set <department> <year> <roadmaps>** - Set user profile.\n"
            "    Example: /profile set CSE 2 python,javascript\n"
            "30. **/progress [type]** - Track roadmap or study plan progress.\n"
            "    Example: /progress python\n"
            "31. **/recommend** - Get personalized resources, jobs, and mentors.\n"
            "\n*Note*: Some features (e.g., /leaderboard, /career) are coming soon due to data access limitations. Use inline buttons for quick actions."
        )
        await update.message.reply_text(help_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error in help command: {e}")
        await update.message.reply_text("Error displaying help. Please try again or contact support.")

async def calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting another calendar.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(CalendarSpider)
        process.start()

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
        await update.message.reply_text("Error fetching calendar. Try again or use /help for details.")

async def resources(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        keyword = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting resources.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(ResourcesSpider)
        process.start()

        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
        profile = c.fetchone()
        conn.close()

        if temp_resources:
            response = "Open-Source Learning Resources:\n"
            filtered_resources = temp_resources
            if keyword or (profile and profile[0]):
                filter_terms = [keyword] if keyword else profile[0].split(',')
                filtered_resources = [r for r in temp_resources if any(term in r['title'].lower() for term in filter_terms)]
            for resource in filtered_resources[:5]:
                response += f"- {resource['title']} ({resource['platform']}): {resource['link']}\n"
        else:
            response = "Unable to fetch resources. Using mock data:\n"
            for resource in MOCK_RESOURCES:
                response += f"- {resource['title']} ({resource['platform']}): {resource['link']}\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in resources command: {e}")
        await update.message.reply_text("Error fetching resources. Try again or use /help for details.")

async def trending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        language = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting trending repositories.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(TrendingSpider, language=language)
        process.start()

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
        await update.message.reply_text("Error fetching trending repos. Try again or use /help for details.")

async def stackoverflow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        tag = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting Stack Overflow questions.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(StackOverflowSpider, tag=tag)
        process.start()

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
        await update.message.reply_text("Error fetching Stack Overflow questions. Try again or use /help for details.")

async def jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        args = context.args
        keyword = args[0].lower() if args else None
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting jobs.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(JobsSpider)
        process.start()

        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT department, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
        profile = c.fetchone()
        conn.close()

        if temp_jobs:
            response = "Job & Internship Opportunities:\n"
            filtered_jobs = temp_jobs
            if keyword or (profile and profile[1]):
                filter_terms = [keyword, "bangladesh", "uiu"] if keyword else profile[1].split(',') + ["bangladesh", "uiu"]
                filtered_jobs = [j for j in temp_jobs if any(term in j['title'].lower() or term in j['company'].lower() or term in j['location'].lower() for term in filter_terms)]
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
        await update.message.reply_text("Error fetching jobs. Try again or use /help for details.")

async def roadmap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args:
            await update.message.reply_text("Please specify a roadmap, e.g., /roadmap python [beginner|intermediate|advanced]\nSee /help for details.")
            return
        roadmap_type = args[0].lower()
        level = args[1].lower() if len(args) > 1 and args[1].lower() in ['beginner', 'intermediate', 'advanced'] else None

        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting another roadmap.")
            return

        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        query = "SELECT title, steps, resources, projects FROM roadmaps WHERE roadmap_type = ?"
        params = [roadmap_type]
        if level:
            query += " AND level = ?"
            params.append(level)
        c.execute(query, params)
        roadmaps = c.fetchall()

        if not roadmaps or (c.execute("SELECT last_updated FROM roadmaps WHERE roadmap_type = ? LIMIT 1", (roadmap_type,)).fetchone() and time.time() - c.fetchone()[0] > 24*3600):
            try:
                process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
                process.crawl(RoadmapSpider, roadmap_type=roadmap_type)
                process.start()
                c.execute(query, params)
                roadmaps = c.fetchall()
            except Exception as e:
                logger.error(f"Scraping roadmap failed: {e}")
        conn.close()

        if roadmaps:
            response = ""
            for title, steps, resources, projects in roadmaps:
                steps = json.loads(steps)
                resources = json.loads(resources)
                projects = json.loads(projects)
                response += f"{title}:\n\nSteps:\n" + "\n".join(f"- {step}" for step in steps) + \
                            f"\n\nResources:\n" + "\n".join(f"- {res}" for res in resources) + \
                            f"\n\nProjects:\n" + "\n".join(f"- {proj}" for proj in projects) + "\n\n"
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
        await update.message.reply_text("Error fetching roadmap. Try again or use /help for details.")

async def mentor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args or args[0].lower() != "find" or len(args) < 2:
            await update.message.reply_text("Usage: /mentor find [department|expertise]\nExample: /mentor find CSE\nSupported departments: CSE, EEE, Civil Engineering, Business Administration, Economics, Pharmacy, INS, English, EDS, MSJ, BGE\nSee /help for details.")
            return
        query = args[1].lower()

        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting another mentor search.")
            return

        temp_faculty.clear()
        temp_faculty_urls.clear()
        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(FacultySpider)
        process.start()

        if temp_faculty:
            response = f"Mentors for '{query}':\n"
            filtered_faculty = [f for f in temp_faculty if query in f['department'].lower() or query in f['expertise'].lower()]
            if filtered_faculty:
                for f in filtered_faculty:
                    response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
                keyboard = [[InlineKeyboardButton(f"Contact {f['name']}", callback_data=f'contact_{f["name"].replace(" ", "_")}')] 
                           for f in filtered_faculty[:5]]
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                response = f"No mentors found for '{query}'. Using mock data:\n"
                for f in MOCK_FACULTY:
                    if query in f['department'].lower() or query in f['expertise'].lower():
                        response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
                reply_markup = None
        else:
            response = f"No mentors found for '{query}'. Using mock data:\n"
            for f in MOCK_FACULTY:
                if query in f['department'].lower() or query in f['expertise'].lower():
                    response += f"- {f['name']} ({f['designation']}, {f['department']})\n  Email: {f['email']}\n  Phone: {f['phone']}\n  Expertise: {f['expertise']}\n"
            reply_markup = None

        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in mentor command: {e}")
        await update.message.reply_text("Error fetching mentors. Try again or use /help for details.")

async def notice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        if not can_scrape(user_id):
            await update.message.reply_text("Please wait a minute before requesting notices.")
            return

        process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
        process.crawl(NoticeSpider)
        process.start()

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
        await update.message.reply_text("Error fetching notices. Try again or use /help for details.")

async def collab(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args or args[0].lower() != "post":
            await update.message.reply_text("Usage: /collab post 'Your message'\nSee /help for details.")
            return
        post = " ".join(args[1:])
        await update.message.reply_text(f"Collaboration post created: {post}\nComing soon: Team matching feature.")
    except Exception as e:
        logger.error(f"Error in collab command: {e}")
        await update.message.reply_text("Error posting collaboration request. Try again or use /help for details.")

async def events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        keyboard = [[InlineKeyboardButton("Mark Attendance", callback_data='mark_attendance')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        response = "Upcoming UIU Events:\n"
        for event in MOCK_EVENTS:
            response += f"- {event['name']} ({event['date']}): {event['details']}\n"
        await update.message.reply_text(response, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error in events command: {e}")
        await update.message.reply_text("Error fetching events. Try again or use /help for details.")

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
        await update.message.reply_text("Error fetching links. Try again or use /help for details.")

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Coming soon: Developer Recognition Leaderboard.")
    except Exception as e:
        logger.error(f"Error in leaderboard command: {e}")
        await update.message.reply_text("Error fetching leaderboard. Try again or use /help for details.")

async def meetup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Coming soon: UIU student-organized tech meetups and coding jams.")
    except Exception as e:
        logger.error(f"Error in meetup command: {e}")
        await update.message.reply_text("Error fetching meetups. Try again or use /help for details.")

async def internship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Use /jobs to find internships and job opportunities.\nSee /help for details.")
    except Exception as e:
        logger.error(f"Error in internship command: {e}")
        await update.message.reply_text("Error fetching internships. Try again or use /help for details.")

async def cgpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text(
                "Usage: /cgpa course1:grade1 course2:grade2\nExample: /cgpa cse321:A cse322:B+\nSee /help for details."
            )
            return
        grades = {course.split(':')[0]: course.split(':')[1] for course in args}
        grade_points = {'A': 4.0, 'A-': 3.7, 'B+': 3.3, 'B': 3.0, 'B-': 2.7, 'C+': 2.3, 'C': 2.0}
        df = pd.DataFrame(list(grades.items()), columns=['Course', 'Grade'])
        df['Points'] = df['Grade'].map(grade_points)
        if df['Points'].isna().any():
            await update.message.reply_text("Invalid grade(s). Use: A, A-, B+, B, B-, C+, C")
            return
        cgpa = df['Points'].mean()
        await update.message.reply_text(f"Your CGPA: {cgpa:.2f}\n{df.to_string(index=False)}")
    except Exception as e:
        logger.error(f"Error in cgpa command: {e}")
        await update.message.reply_text("Invalid format. Use: /cgpa cse321:A cse322:B+\nSee /help for details.")

async def gpapredict(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) != 2:
            await update.message.reply_text("Usage: /gpapredict current_cgpa target_cgpa\nExample: /gpapredict 3.5 3.8\nSee /help for details.")
            return
        current_cgpa, target_cgpa = float(args[0]), float(args[1])
        response = f"To achieve a target CGPA of {target_cgpa:.2f} from {current_cgpa:.2f}, aim for high grades in remaining courses. Detailed prediction coming soon."
        await update.message.reply_text(response)
    except ValueError:
        await update.message.reply_text("Please provide valid CGPA values, e.g., /gpapredict 3.5 3.8\nSee /help for details.")
    except Exception as e:
        logger.error(f"Error in gpapredict command: {e}")
        await update.message.reply_text("Error predicting CGPA. Try again or use /help for details.")

async def scholarships(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = "Scholarship Opportunities for UIU Students:\n"
        for scholarship in MOCK_SCHOLARSHIPS:
            response += f"- {scholarship['name']}: {scholarship['details']} ({scholarship['link']})\n"
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in scholarships command: {e}")
        await update.message.reply_text("Error fetching scholarships. Try again or use /help for details.")

async def academic_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await calendar(update, context)

async def career(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Coming soon: Career Office updates, CV workshops, and recruitment drives.")
    except Exception as e:
        logger.error(f"Error in career command: {e}")
        await update.message.reply_text("Error fetching career updates. Try again or use /help for details.")

async def fyp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args or args[0].lower() not in ["ideas", "docs"]:
            await update.message.reply_text("Usage: /fyp ideas or /fyp docs\nSee /help for details.")
            return
        if args[0].lower() == "ideas":
            await update.message.reply_text("Coming soon: Trending FYP ideas from UIU alumni.")
        elif args[0].lower() == "docs":
            await update.message.reply_text("Coming soon: FYP guidelines, formats, and past submissions.")
    except Exception as e:
        logger.error(f"Error in fyp command: {e}")
        await update.message.reply_text("Error fetching FYP info. Try again or use /help for details.")

async def studyplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if len(args) < 3:
            await update.message.reply_text("Usage: /studyplan course1,course2 hours_per_week target_date(YYYY-MM-DD) priority(course1:1,cse322:2)\nSee /help for details.")
            return
        courses, hours, target_date, priority = args[0].split(','), int(args[1]), args[2], args[3]
        plan = StudyPlan(courses=courses, hours_per_week=hours, target_date=target_date, priority=priority)
        priorities = {p.split(':')[0]: int(p.split(':')[1]) for p in priority.split(',')}
        df = pd.DataFrame(plan.courses, columns=['Course'])
        df['Priority'] = df['Course'].map(priorities)
        df['Hours'] = df['Priority'].apply(lambda x: (plan.hours_per_week * (3 - x)) / len(plan.courses))
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("INSERT INTO progress (user_id, roadmap_type, level, completed_steps) VALUES (?, ?, ?, ?)",
                  (update.effective_user.id, 'studyplan', 'current', json.dumps([])))
        conn.commit()
        conn.close()
        response = f"Study Plan for {plan.target_date}:\n{df.to_string(index=False)}\nTotal Hours/Week: {plan.hours_per_week}"
        await update.message.reply_text(response)
    except ValidationError as e:
        await update.message.reply_text(f"Invalid input: {e}\nSee /help for details.")
    except Exception as e:
        logger.error(f"Error in studyplan command: {e}")
        await update.message.reply_text("Error creating study plan. Try again or use /help for details.")

async def reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /reminders add task deadline(YYYY-MM-DD) [recurrence] or /reminders list\nSee /help for details.")
            return
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        if args[0].lower() == "add":
            task, deadline = " ".join(args[1:-1]), args[-1]
            recurrence = args[-1] if len(args) > 2 and args[-2] in ['daily', 'weekly'] else 'none'
            if recurrence != 'none':
                deadline = args[-2]
            try:
                datetime.strptime(deadline, '%Y-%m-%d')
            except ValueError:
                await update.message.reply_text("Invalid date format. Use YYYY-MM-DD, e.g., 2025-09-01")
                return
            c.execute("INSERT INTO reminders (user_id, task, deadline, recurrence) VALUES (?, ?, ?, ?)", 
                      (user_id, task, deadline, recurrence))
            conn.commit()
            scheduler = AsyncIOScheduler()
            if recurrence == 'daily':
                scheduler.add_job(send_reminder, 'interval', days=1, start_date=datetime.strptime(deadline, '%Y-%m-%d'),
                                 args=[context.bot, user_id, task])
            elif recurrence == 'weekly':
                scheduler.add_job(send_reminder, 'interval', weeks=1, start_date=datetime.strptime(deadline, '%Y-%m-%d'),
                                 args=[context.bot, user_id, task])
            else:
                scheduler.add_job(send_reminder, 'date', run_date=datetime.strptime(deadline, '%Y-%m-%d'),
                                 args=[context.bot, user_id, task])
            scheduler.start()
            await update.message.reply_text(f"Reminder set: {task} on {deadline} ({recurrence})")
        else:
            c.execute("SELECT task, deadline, recurrence FROM reminders WHERE user_id = ?", (user_id,))
            reminders = c.fetchall()
            response = "Your Reminders:\n" + "\n".join(f"- {task} ({deadline}, {recurrence})" for task, deadline, recurrence in reminders)
            await update.message.reply_text(response or "No reminders set.")
        conn.close()
    except Exception as e:
        logger.error(f"Error in reminders command: {e}")
        await update.message.reply_text("Error setting/listing reminders. Try again or use /help for details.")

async def send_reminder(bot, user_id, task):
    try:
        await bot.send_message(user_id, f"Reminder: {task} is due today!")
    except Exception as e:
        logger.error(f"Error sending reminder: {e}")

async def motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT favorite_roadmaps, department FROM user_profiles WHERE user_id = ?", (update.effective_user.id,))
        profile = c.fetchone()
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
        await update.message.reply_text("Error fetching motivational tip. Try again or use /help for details.")

async def codeshare(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        if not args:
            await update.message.reply_text("Usage: /codeshare add 'description' 'tags' 'code' or /codeshare list [tag]\nSee /help for details.")
            return
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        if args[0].lower() == "add":
            if len(args) < 3:
                await update.message.reply_text("Usage: /codeshare add 'description' 'tags' 'code'\nSee /help for details.")
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
        conn.close()
    except Exception as e:
        logger.error(f"Error in codeshare command: {e}")
        await update.message.reply_text("Error handling code snippets. Try again or use /help for details.")

async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        if not args or args[0].lower() != "set" or len(args) < 4:
            await update.message.reply_text("Usage: /profile set department year favorite_roadmaps\nExample: /profile set CSE 2 python,javascript\nSee /help for details.")
            return
        profile = UserProfile(department=args[1], year=int(args[2]), favorite_roadmaps=args[3])
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO user_profiles (user_id, department, year, favorite_roadmaps) VALUES (?, ?, ?, ?)",
                  (user_id, profile.department, profile.year, profile.favorite_roadmaps))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"Profile updated: {profile.department}, Year {profile.year}, Roadmaps: {profile.favorite_roadmaps}")
    except ValidationError as e:
        await update.message.reply_text(f"Invalid input: {e}\nSee /help for details.")
    except Exception as e:
        logger.error(f"Error in profile command: {e}")
        await update.message.reply_text("Error setting profile. Try again or use /help for details.")

async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        args = context.args
        user_id = update.effective_user.id
        roadmap_type = args[0].lower() if args else 'studyplan'
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT roadmap_type, level, completed_steps FROM progress WHERE user_id = ? AND roadmap_type = ?",
                  (user_id, roadmap_type))
        progress = c.fetchall()
        conn.close()
        if progress:
            response = f"Progress on {roadmap_type}:\n"
            for rt, level, steps in progress:
                steps = json.loads(steps)
                response += f"- {level}: {len(steps)} steps completed\n"
            await update.message.reply_text(response)
        else:
            await update.message.reply_text(f"No progress tracked for {roadmap_type}. Start with /studyplan or /roadmap.\nSee /help for details.")
    except Exception as e:
        logger.error(f"Error in progress command: {e}")
        await update.message.reply_text("Error fetching progress. Try again or use /help for details.")

async def recommend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        conn = sqlite3.connect('uiu_bot.db', timeout=10)
        c = conn.cursor()
        c.execute("SELECT department, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
        profile = c.fetchone()
        conn.close()
        response = "Recommended Resources & Opportunities:\n"
        if profile and profile[1]:
            roadmaps, dept = profile[1], profile[0]
            response += f"For {roadmaps.split(',')[0]} and {dept}:\n"
            if temp_resources:
                filtered_resources = [r for r in temp_resources if any(term in r['title'].lower() for term in roadmaps.split(','))]
                for resource in filtered_resources[:3]:
                    response += f"- Resource: {resource['title']} ({resource['platform']}): {resource['link']}\n"
            if temp_trending:
                filtered_trending = [r for r in temp_trending if any(term in r['language'].lower() for term in roadmaps.split(','))]
                for repo in filtered_trending[:3]:
                    response += f"- Repo: {repo['repo']} ({repo['language']}): {repo['description']}\n"
            if temp_jobs:
                filtered_jobs = [j for j in temp_jobs if any(term in j['title'].lower() or term in j['company'].lower() for term in roadmaps.split(','))]
                for job in filtered_jobs[:3]:
                    response += f"- Job: {job['title']} at {job['company']} ({job['location']}): {job['link']}\n"
            temp_faculty.clear()
            temp_faculty_urls.clear()
            try:
                process = CrawlerProcess({'USER_AGENT': 'Mozilla/5.0'})
                process.crawl(FacultySpider)
                process.start()
                if temp_faculty:
                    filtered_faculty = [f for f in temp_faculty if dept.lower() in f['department'].lower() or roadmaps.split(',')[0] in f['expertise'].lower()]
                    if filtered_faculty:
                        response += "Suggested Mentors:\n" + "\n".join(f"- {f['name']} ({f['email']})" for f in filtered_faculty[:3])
            except Exception as e:
                logger.error(f"Scraping faculty for recommend failed: {e}")
                response += "Suggested Mentors:\n" + "\n".join(f"- {f['name']} ({f['email']})" for f in MOCK_FACULTY if dept.lower() in f['department'].lower() or roadmaps.split(',')[0] in f['expertise'].lower())
        else:
            response += "Set your profile with /profile to get personalized recommendations.\nSee /help for details."
        await update.message.reply_text(response)
    except Exception as e:
        logger.error(f"Error in recommend command: {e}")
        await update.message.reply_text("Error fetching recommendations. Try again or use /help for details.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        await query.answer()
        if query.data.startswith('roadmap_'):
            _, roadmap_type, level = query.data.split('_')
            conn = sqlite3.connect('uiu_bot.db', timeout=10)
            c = conn.cursor()
            c.execute("SELECT title, steps, resources, projects FROM roadmaps WHERE roadmap_type = ? AND level = ?",
                      (roadmap_type, level))
            roadmap = c.fetchone()
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
            c = conn.cursor()
            c.execute("SELECT department, year, favorite_roadmaps FROM user_profiles WHERE user_id = ?", (user_id,))
            profile = c.fetchone()
            conn.close()
            if profile:
                dept, year, roadmaps = profile
                await query.message.reply_text(f"Profile:\nDepartment: {dept}\nYear: {year}\nFavorite Roadmaps: {roadmaps}")
            else:
                await query.message.reply_text("No profile set. Use /profile set dept year roadmaps\nSee /help for details.")
        elif query.data == 'set_profile':
            await query.message.reply_text("Set your profile with /profile set department year favorite_roadmaps\nExample: /profile set CSE 2 python,javascript\nSee /help for details.")
        elif query.data == 'mark_attendance':
            await query.message.reply_text("Coming soon: Event attendance marking feature.")
        elif query.data == 'add_reminder_calendar':
            await query.message.reply_text("To add a calendar event reminder, use /reminders add 'Event Name' YYYY-MM-DD [recurrence]\nSee /help for details.")
        elif query.data == 'add_reminder_job':
            await query.message.reply_text("To add a job application reminder, use /reminders add 'Apply for Job Title' YYYY-MM-DD\nSee /help for details.")
        elif query.data == 'help':
            await help(update, context)
    except Exception as e:
        logger.error(f"Error in button callback: {e}")
        await query.message.reply_text("Error processing your request. Try again or use /help for details.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}")
    if update and update.message:
        await update.message.reply_text(f"An error occurred: {str(context.error)}. Please try again or use /help for details.")

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

# Setup Telegram application and webhook with retry
async def setup_application():
    global application
    retries = 3
    for attempt in range(retries):
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
            application.add_handler(CommandHandler("collab", collab))
            application.add_handler(CommandHandler("events", events))
            application.add_handler(CommandHandler("links", links))
            application.add_handler(CommandHandler("leaderboard", leaderboard))
            application.add_handler(CommandHandler("meetup", meetup))
            application.add_handler(CommandHandler("internship", internship))
            application.add_handler(CommandHandler("cgpa", cgpa))
            application.add_handler(CommandHandler("gpapredict", gpapredict))
            application.add_handler(CommandHandler("scholarships", scholarships))
            application.add_handler(CommandHandler("academic", academic_calendar))
            application.add_handler(CommandHandler("career", career))
            application.add_handler(CommandHandler("fyp", fyp))
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

            # Check current webhook
            webhook_info = await application.bot.get_webhook_info()
            if webhook_info.url != WEBHOOK_URL:
                await application.bot.delete_webhook(drop_pending_updates=True)
                await application.bot.set_webhook(url=WEBHOOK_URL)
                logger.info(f"Webhook set to {WEBHOOK_URL}")
            else:
                logger.info(f"Webhook already set to {WEBHOOK_URL}")
            return
        except Exception as e:
            logger.error(f"Setup attempt {attempt + 1} failed: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(2)
            else:
                raise

# Main function to run webhook server
async def main():
    try:
        # Create aiohttp app
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

        # Keep the application running
        while True:
            await asyncio.sleep(3600)
    except Exception as e:
        logger.error(f"Error in main: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
