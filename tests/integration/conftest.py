from dotenv import load_dotenv

# Runs at collection time, before the pytestmark skipif in each test module
# reads os.environ — otherwise credentials set only via .env (not a real
# exported env var) would never be seen and every test here would skip.
load_dotenv()
