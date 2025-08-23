UIU Buddy Telegram Bot

UIU Buddy is a Telegram bot designed specifically for United International University (UIU) students, offering tools to enhance academic collaboration, campus connectivity, and career preparation. It connects students for study groups, ride-sharing, and academic planning, while providing access to UIU resources and Developer Hub information. Tailored for departments like CSE, BBA, Pharmacy, and Economics, UIU Buddy empowers students to excel in their academic and campus life.

Live Bot: https://t.me/UIUDeveloperHubBot

Features
- Study Partner Matching (/study find <course>): Connects students taking the same course (e.g., /study find cse321), sharing section, location, and contact info (Telegram, Facebook, WhatsApp). Integrates X posts near Dhaka and Wikipedia summaries for course topics.
- Section-Specific Matching (/match <course> <section>): Finds peers in the same course and section (e.g., /match cse321 A), including contact details for coordination.
- Ride Sharing (/ride share <from> <to> <time>): Matches students for ride-sharing (e.g., /ride share Dhanmondi UIU 08:00) and sends auto-notifications to opted-in users matching the location, with X post integration.
- Academic Calendar (/calendar): Displays UIU academic events (exams, hackathons) scraped from the university website, with mock data fallback and reminder options.
- Learning Resources (/resources [keyword]): Provides curated open-source learning materials (e.g., freeCodeCamp, edX) filtered by keywords (e.g., /resources python), with Wikipedia summaries.
- CGPA Calculator (/cgpa <course:grade>): Calculates CGPA based on course grades (e.g., /cgpa cse321:A cse322:B+).
- Study Plan Creation (/studyplan <courses> <hours> <date> <priority>): Generates prioritized study plans (e.g., /studyplan cse321,cse322 10 2025-12-01 cse321:1,cse322:2).
- Reminders (/reminders add/list <task> <deadline>): Sets and manages academic reminders (e.g., /reminders add Meet Dr. Suman 2025-09-01).
- Motivational Tips (/motivate): Delivers personalized motivational messages based on user profiles.
- Profile Management (/profile set): Stores user details (department, year, courses, section, contacts, ride-share opt-in), with automatic deletion after 5 months of inactivity (e.g., /profile set CSE 2 python,dsa cse321,cse322 A telegram:@user,fb:user,wa:1234567890 1).
- UIU Developer Hub Info (/about): Shares details about the UIU Developer Hub, scraped from the university website or mock data, with a link to its Facebook page.
- Help Command (/help): Lists all commands concisely for quick reference.

Setup

Prerequisites
- Python 3.8+
- Telegram account
- Bot token from https://t.me/BotFather
- Server or hosting platform (e.g., Render, Heroku) for webhook deployment
- SQLite for database storage

Installation
1. Clone the Repository:

       git clone https://github.com/your-username/uiu-buddy-bot.git


       cd uiu-buddy-bot

3. Install Dependencies:
   Install required Python packages listed in requirements.txt:
   
        pip install -r requirements.txt
   Install snscrape for X scraping:
   
        sudo apt install -y snscrape
   Install libxml2-dev and libxslt1-dev for lxml:

         sudo apt install -y libxml2-dev libxslt1-dev

5. Set Environment Variables:
   Create a .env file or set the following environment variables:
   
       export BOT_TOKEN="your-telegram-bot-token"
       export WEBHOOK_URL="https://your-app-url.onrender.com/webhook"
       export PORT=8443
   - Obtain BOT_TOKEN by creating a bot via https://t.me/BotFather using the /newbot command.

7. Initialize Database:
   The bot uses SQLite (uiu_buddy.db) to store user profiles, study plans, reminders, peer matches, and ride-share requests. The database is initialized automatically on startup.

8. Run the Bot:
   Start the bot and webhook server:
   
       python app.py
   The bot will set up a webhook at WEBHOOK_URL and run on PORT.


Usage
1. Start the bot: /start
2. View commands: /help
3. Example commands:
   - /study find cse321: Find study partners for CSE321.
   - /match cse321 A: Find peers in section A of CSE321.
   - /ride share Dhanmondi UIU 08:00: Coordinate a ride from Dhanmondi to UIU.
   - /calendar: View UIU academic events.
   - /resources python: Get Python learning resources.
   - /cgpa cse321:A cse322:B+: Calculate CGPA.
   - /studyplan cse321,cse322 10 2025-12-01 cse321:1,cse322:2: Create a study plan.
   - /reminders add Meet Dr. Suman 2025-09-01: Set a reminder.
   - /profile set CSE 2 python,dsa cse321,cse322 A telegram:@user,fb:user,wa:1234567890 1: Set profile.
   - /motivate: Get motivational tips.
   - /about: Learn about the UIU Developer Hub.

Architecture
- Framework: Built with python-telegram-bot for seamless Telegram API interaction.
- Database: SQLite stores user profiles, study plans, reminders, peer matches, and ride-share requests.
- Scraping: Uses trafilatura for UIU website data, snscrape for X posts near Dhaka, and wikipedia-api/wikipedia for course summaries.
- Data Processing: Employs polars for efficient CGPA calculations and study plan generation.
- Matching: Leverages rapidfuzz for fuzzy matching of courses, sections, and locations.
- Rate Limiting: Implements a 30-second cooldown for scraping commands to prevent abuse.

Contributing
Contributions are welcome! To contribute:
1. Fork the repository.
2. Create a feature branch (git checkout -b feature/your-feature).
3. Commit changes (git commit -m "Add your feature").
4. Push to the branch (git push origin feature/your-feature).
5. Open a pull request.
Please ensure code follows PEP 8 standards and includes tests where applicable.

License
This project is licensed under the MIT License. See the LICENSE file for details.

Contact
For support or feature requests, contact the UIU Developer Hub via https://www.facebook.com/uiudevelopershub or start a chat with the bot: https://t.me/UIUDeveloperHubBot.
