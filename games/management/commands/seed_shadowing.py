from django.core.management.base import BaseCommand

from games.models import ShadowingText

PASSAGES = [
    {
        'title': 'A Nice Afternoon',
        'topic': 'Movies',
        'level': 'A2',
        'body': (
            "It was a nice afternoon. I saw a movie about a brave actor. "
            "He was in an action film. This activity was very fun. "
            "My friend agrees with me. We want to watch it again after dinner."
        ),
    },
    {
        'title': 'A Trip to the Mountains',
        'topic': 'Travel',
        'level': 'A2',
        'body': (
            "Last summer we drove to the mountains for a short trip. "
            "The air was cool and the view was amazing. "
            "We walked along a quiet path and took many photos of the lake."
        ),
    },
    {
        'title': 'My Morning Coffee',
        'topic': 'Daily life',
        'level': 'A1',
        'body': (
            "Every morning I wake up early and make a cup of coffee. "
            "I sit by the window and watch the street. "
            "It is my favorite part of the day."
        ),
    },
    {
        'title': 'Street Food in Asia',
        'topic': 'Food',
        'level': 'B1',
        'body': (
            "Street food markets in Asia are famous for their bright colours and strong smells. "
            "Vendors cook fresh dishes right in front of hungry customers. "
            "Trying a new snack from a small stall is often the best part of the trip."
        ),
    },
    {
        'title': 'The Rise of Smart Homes',
        'topic': 'Technology',
        'level': 'B2',
        'body': (
            "Smart home devices have quietly changed the way people live. "
            "A single voice command can now adjust the lights, lock the door, or start the coffee machine. "
            "Some experts believe this convenience comes at the cost of privacy, since every device is always listening."
        ),
    },
    {
        'title': 'Life in the Rainforest',
        'topic': 'Nature',
        'level': 'B1',
        'body': (
            "The rainforest is home to millions of different species of plants and animals. "
            "Tall trees block most of the sunlight, so the forest floor stays dark and damp all year. "
            "Scientists still discover new insects and frogs there every single year."
        ),
    },
    {
        'title': 'The Final Match',
        'topic': 'Sports',
        'level': 'A2',
        'body': (
            "The stadium was full of excited fans waiting for the final match to begin. "
            "Both teams had trained hard all season for this one game. "
            "When the whistle blew, everyone stood up and started cheering loudly."
        ),
    },
    {
        'title': 'Working From Home',
        'topic': 'Work',
        'level': 'B2',
        'body': (
            "Working from home gives people more freedom over their schedule, but it also blurs the line between work and rest. "
            "Without a proper routine, many remote workers find themselves answering emails late into the evening. "
            "Setting clear boundaries has become an essential skill in modern careers."
        ),
    },
    {
        'title': 'A Letter to a Friend',
        'topic': 'Daily life',
        'level': 'A1',
        'body': (
            "Dear friend, how are you? I am fine. "
            "I have a new job and a new house. "
            "I hope we can meet soon and talk about everything."
        ),
    },
    {
        'title': 'The Future of Space Travel',
        'topic': 'Technology',
        'level': 'C1',
        'body': (
            "Private companies are now competing to make space travel affordable for ordinary citizens, not just trained astronauts. "
            "Reusable rockets have already reduced launch costs dramatically over the past decade. "
            "Whether this progress will lead to permanent settlements beyond Earth remains an open and fascinating question."
        ),
    },
]


class Command(BaseCommand):
    help = 'Seed a starter set of Shadowing passages across topics and CEFR levels'

    def handle(self, *args, **options):
        created = 0
        for p in PASSAGES:
            _, was_created = ShadowingText.objects.get_or_create(
                title=p['title'],
                defaults={'topic': p['topic'], 'level': p['level'], 'body': p['body'].strip()},
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f'Seeded {created} new shadowing passages ({len(PASSAGES)} total defined).'))
