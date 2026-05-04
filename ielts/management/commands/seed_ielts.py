"""
Management command to seed sample IELTS Reading and Listening test data.
Based on Otaboyev IELTS Prep style — names slightly modified.

Usage:
  python manage.py seed_ielts
  python manage.py seed_ielts --clear   (delete existing data first)
"""

from django.core.management.base import BaseCommand
from django.db import transaction


READING_TESTS = [
    # ──────────────────────────────────────────────────────────────────────
    # FULL MOCK TESTS (3 parts)
    # ──────────────────────────────────────────────────────────────────────
    {
        "title": "Academic Reading Mock Test 1",
        "test_type": "FULL_MOCK",
        "difficulty": "MEDIUM",
        "is_premium": False,
        "parts": [
            {
                "passage_number": 1,
                "title": "The Origins of Coffee",
                "content": """Coffee, one of the world's most popular beverages, has a rich history that spans several centuries. The story of coffee begins in the Ethiopian highlands, where, according to legend, a goat herder named Kaldi first discovered the potential of these beloved beans. The legend states that Kaldi noticed that his goats did not sleep at night after eating berries from a certain tree, and that his goats were very energetic.

Kaldi reportedly brought the berries to an Islamic monastery and made a drink with them, and found that it kept him alert through the long hours of evening prayer. Pleased with this discovery, the abbot shared his findings with the other monks at the monastery.

As word moved east and coffee reached the Arabian Peninsula, it began a journey which would bring these beans across the globe. The Arabian Peninsula was where coffee cultivation and trade first began. Coffee plants were cultivated in the Yemeni district of Arabia and by the 15th century, coffee was being grown in the Yemeni district of Arabia and by the 16th century it was known in the rest of the Middle East, Persia, Turkey, and northern Africa.

Coffee houses, known as qahveh khaneh, began appearing in the Near East. The popularity of the coffee houses was unequalled and people frequented them for all kinds of social activity. Not only did the patrons drink coffee and engage in conversation, but they also listened to music, watched performers, played chess and kept current on the news of the day. Coffee houses quickly became such an important center for the exchange of information that they were often called 'Schools of the Wise'.

With thousands of pilgrims visiting the holy city of Mecca each year from all over the world, knowledge of this 'wine of Araby' began to spread. Coffee eventually made its way to Europe through Venetian traders, where it became an extremely popular drink in the mid-17th century.""",
                "questions": [
                    {"number": 1, "question_type": "TFNG", "content": "The goat herder Kaldi first discovered coffee berries in Ethiopia.", "correct_answer": "TRUE", "explanation": "The first paragraph states the story begins in the Ethiopian highlands with Kaldi."},
                    {"number": 2, "question_type": "TFNG", "content": "The monks at the monastery were unhappy with Kaldi's discovery.", "correct_answer": "NOT GIVEN", "explanation": "The text doesn't say the monks were unhappy — only that the abbot shared the findings."},
                    {"number": 3, "question_type": "TFNG", "content": "Coffee cultivation began in the Yemeni district of Arabia in the 15th century.", "correct_answer": "TRUE", "explanation": "Paragraph 3 confirms: 'coffee was being grown in the Yemeni district of Arabia by the 15th century'."},
                    {"number": 4, "question_type": "TFNG", "content": "Coffee houses were also used for playing chess.", "correct_answer": "TRUE", "explanation": "Paragraph 4 lists chess as one of the activities."},
                    {"number": 5, "question_type": "MCQ", "content": "What does the phrase 'Schools of the Wise' refer to?", "correct_answer": "B",
                     "choices": [{"option": "A", "text": "Universities in Arabia"}, {"option": "B", "text": "Coffee houses where people exchanged information"}, {"option": "C", "text": "Religious monasteries"}, {"option": "D", "text": "Libraries in Turkey"}]},
                    {"number": 6, "question_type": "GAP", "content": "Coffee reached Europe through ___ traders in the mid-17th century.", "correct_answer": "VENETIAN"},
                    {"number": 7, "question_type": "SHORT", "content": "In which country did coffee cultivation first begin?", "correct_answer": "YEMEN"},
                ],
            },
            {
                "passage_number": 2,
                "title": "The Development of Solar Energy",
                "content": """Solar energy has become one of the fastest-growing sources of renewable energy in the world. The Sun provides an enormous amount of energy — in one hour, the amount of power from the Sun that strikes the Earth is more than the entire world consumes in a year. Yet for many years, the development of solar technology was hindered by high costs and low efficiency.

The photovoltaic (PV) effect was first observed by the French physicist Alexandre-Edmond Becquerel in 1839. However, it was not until 1954 that Bell Laboratories in the United States developed the first practical silicon solar cell. This early cell converted about 6% of sunlight into electricity — a figure that has been dramatically improved upon since then.

Through the 1960s and 1970s, solar panels were primarily used in space applications. NASA and other space agencies required reliable, lightweight power sources for satellites and spacecraft, making the expensive technology worthwhile for these specific applications. The 1973 oil crisis, however, sparked interest in alternative energy sources and led to increased government funding for solar research.

By the 1990s, the price of solar panels had fallen dramatically, making them more accessible for residential and commercial use. Germany became an early leader in solar adoption, implementing feed-in tariff programmes that guaranteed fixed payments to households and businesses generating solar electricity. This policy created a stable market and encouraged rapid growth of the solar industry.

The 21st century has seen solar energy transform from a niche technology into a mainstream power source. The cost of solar panels has dropped by more than 90% since 2010, driven by advances in manufacturing technology and economies of scale. In some regions, solar power is now the cheapest source of electricity ever recorded, undercutting even coal and natural gas.""",
                "questions": [
                    {"number": 8, "question_type": "TFNG", "content": "The Sun produces enough energy in one hour to power the world for an entire year.", "correct_answer": "TRUE", "explanation": "This is directly stated in paragraph 1."},
                    {"number": 9, "question_type": "TFNG", "content": "Alexandre-Edmond Becquerel was American.", "correct_answer": "FALSE", "explanation": "The text says he was a French physicist."},
                    {"number": 10, "question_type": "TFNG", "content": "The first silicon solar cell converted 10% of sunlight into electricity.", "correct_answer": "FALSE", "explanation": "The first cell converted about 6%, not 10%."},
                    {"number": 11, "question_type": "MCQ", "content": "What triggered increased government funding for solar research in the 1970s?", "correct_answer": "C",
                     "choices": [{"option": "A", "text": "Advances in satellite technology"}, {"option": "B", "text": "German government subsidies"}, {"option": "C", "text": "The 1973 oil crisis"}, {"option": "D", "text": "Low costs of solar panels"}]},
                    {"number": 12, "question_type": "GAP", "content": "Germany introduced ___ programmes that guaranteed fixed payments for solar electricity.", "correct_answer": "FEED-IN TARIFF"},
                    {"number": 13, "question_type": "SHORT", "content": "By approximately what percentage did the cost of solar panels drop between 2010 and the time of writing?", "correct_answer": "90%"},
                ],
            },
            {
                "passage_number": 3,
                "title": "Marine Ecosystems and Climate Change",
                "content": """The world's oceans cover approximately 71% of the Earth's surface and contain 97% of the planet's water. These vast bodies of water play a critical role in regulating the Earth's climate, producing oxygen, and sustaining countless species of marine life. However, climate change is causing significant disruptions to marine ecosystems that scientists are only beginning to fully understand.

Ocean temperatures have risen by an average of 0.13°C per decade since 1901, according to the Intergovernmental Panel on Climate Change (IPCC). While this may seem modest, even small changes in ocean temperature can have profound effects on marine life. Coral reefs, for example, are particularly sensitive to temperature changes. When temperatures rise above the corals' tolerance level for an extended period, the corals expel the algae living in their tissues, causing them to turn white — a phenomenon known as coral bleaching.

Ocean acidification is another major consequence of climate change. As the concentration of carbon dioxide in the atmosphere increases, the oceans absorb more CO₂, which reacts with seawater to form carbonic acid. Since pre-industrial times, ocean pH has decreased by 0.1 units, representing a 26% increase in acidity. This acidification makes it more difficult for shell-forming organisms such as oysters, mussels, and certain plankton species to build and maintain their shells.

Sea level rise, driven by the thermal expansion of warming seawater and the melting of ice sheets, poses additional threats to coastal marine habitats. Mangrove forests and seagrass meadows, which serve as important nurseries for many marine species, are particularly vulnerable. These habitats cannot migrate inland quickly enough to keep pace with rising sea levels.

Despite these challenges, marine ecosystems have demonstrated remarkable resilience. Some coral species have shown the ability to adapt to warmer temperatures, and marine protected areas have been shown to enhance the recovery of damaged ecosystems. Scientists argue that reducing greenhouse gas emissions remains the most effective strategy for preserving marine biodiversity in the long term.""",
                "questions": [
                    {"number": 14, "question_type": "YNNG", "content": "The author believes climate change has no significant impact on marine ecosystems.", "correct_answer": "NO", "explanation": "The entire passage describes significant impacts."},
                    {"number": 15, "question_type": "YNNG", "content": "Coral bleaching occurs when corals expel algae from their tissues.", "correct_answer": "YES", "explanation": "Paragraph 2 confirms this directly."},
                    {"number": 16, "question_type": "TFNG", "content": "Ocean pH has decreased by 0.26 units since pre-industrial times.", "correct_answer": "FALSE", "explanation": "The text says pH decreased by 0.1 units (a 26% increase in acidity)."},
                    {"number": 17, "question_type": "MCQ", "content": "What process causes ocean acidification?", "correct_answer": "A",
                     "choices": [{"option": "A", "text": "CO₂ dissolves in seawater to form carbonic acid"}, {"option": "B", "text": "Thermal expansion of seawater"}, {"option": "C", "text": "Melting of polar ice sheets"}, {"option": "D", "text": "Reduced oxygen production"}]},
                    {"number": 18, "question_type": "MATCH", "content": "Which habitat is described as a nursery for many marine species?", "correct_answer": "MANGROVE"},
                    {"number": 19, "question_type": "GAP", "content": "Scientists argue that reducing ___ emissions is the most effective long-term strategy.", "correct_answer": "GREENHOUSE GAS"},
                    {"number": 20, "question_type": "SHORT", "content": "What percentage of Earth's surface is covered by oceans?", "correct_answer": "71%"},
                ],
            },
        ],
    },
    {
        "title": "Academic Reading Mock Test 2",
        "test_type": "FULL_MOCK",
        "difficulty": "HARD",
        "is_premium": False,
        "parts": [
            {
                "passage_number": 1,
                "title": "The Psychology of Decision Making",
                "content": """Human decision making is a complex cognitive process that researchers have studied for decades. Classical economic theory assumed that humans are rational agents who always make decisions to maximise their utility. However, research by psychologists Daniel Kahneman and Amos Tversky fundamentally challenged this view, demonstrating that human judgment is systematically biased.

Kahneman proposed a dual-process theory of thinking, popularised in his book 'Thinking, Fast and Slow'. System 1 thinking is fast, automatic, and intuitive — it operates below the level of conscious awareness and is responsible for snap judgments and emotional reactions. System 2 thinking is slow, deliberate, and analytical — it requires conscious effort and is used for complex calculations and careful reasoning.

The anchoring effect is one of the most robust cognitive biases documented by researchers. When people are exposed to an initial piece of information (the anchor) before making a judgment, their subsequent assessments tend to be influenced by this initial value, even when the anchor is arbitrary or irrelevant. In one study, participants were asked to spin a wheel that stopped at either 10 or 65, and were then asked to estimate the percentage of African countries in the United Nations. Those who spun 65 gave significantly higher estimates than those who spun 10.

Loss aversion is another well-documented cognitive bias. People tend to feel the pain of losses more intensely than the pleasure of equivalent gains. Research suggests that losses feel approximately twice as powerful as gains. This explains why people tend to hold onto failing investments too long, hoping to recover their losses, rather than cutting their losses and reinvesting in more promising opportunities.

The availability heuristic describes our tendency to judge the likelihood of events based on how easily examples come to mind. People overestimate the frequency of dramatic, memorable events such as aeroplane crashes, and underestimate more common but less memorable causes of death such as car accidents. This bias has important implications for public policy and risk communication.""",
                "questions": [
                    {"number": 1, "question_type": "TFNG", "content": "Classical economic theory viewed humans as always making rational decisions.", "correct_answer": "TRUE"},
                    {"number": 2, "question_type": "TFNG", "content": "System 1 thinking requires significant conscious effort.", "correct_answer": "FALSE", "explanation": "System 1 is described as fast and automatic, operating below conscious awareness."},
                    {"number": 3, "question_type": "TFNG", "content": "In Kahneman's spinning wheel study, participants who saw the number 65 gave lower estimates.", "correct_answer": "FALSE", "explanation": "They gave significantly HIGHER estimates."},
                    {"number": 4, "question_type": "MCQ", "content": "According to the passage, how much more powerfully do losses feel compared to equivalent gains?", "correct_answer": "B",
                     "choices": [{"option": "A", "text": "Three times as powerful"}, {"option": "B", "text": "Approximately twice as powerful"}, {"option": "C", "text": "Equally as powerful"}, {"option": "D", "text": "1.5 times as powerful"}]},
                    {"number": 5, "question_type": "GAP", "content": "The tendency to hold losing investments is explained by ___ aversion.", "correct_answer": "LOSS"},
                    {"number": 6, "question_type": "SHORT", "content": "What is the name of Kahneman's book that popularised dual-process theory?", "correct_answer": "THINKING FAST AND SLOW"},
                    {"number": 7, "question_type": "YNNG", "content": "The availability heuristic has implications for public policy.", "correct_answer": "YES"},
                ],
            },
            {
                "passage_number": 2,
                "title": "Urbanisation and Its Consequences",
                "content": """The world is undergoing the largest wave of urban growth in history. Today, more than half of the world's people live in towns and cities, and by 2030 this number is expected to swell to about 5 billion. Much of the urbanisation occurring will take place in Africa and Asia, particularly in the region's smaller settlements and secondary cities.

Cities offer many advantages. Proximity to other people, businesses, and public services creates economic opportunities and facilitates the spread of ideas and innovation. Urban areas typically have higher productivity per worker than rural areas, and urban residents generally have higher incomes and better access to education and healthcare. This has led many economists to argue that urbanisation is closely linked to economic development.

However, rapid and unplanned urbanisation can also create significant challenges. In many developing countries, cities are struggling to provide adequate housing, clean water, sanitation, and transportation infrastructure for their rapidly growing populations. When cities cannot keep pace with this growth, informal settlements — also known as slums or shanty towns — can expand rapidly. Currently, around one billion people worldwide live in slums, many without access to basic services.

Urban areas also contribute disproportionately to climate change. Cities account for approximately 70% of global carbon dioxide emissions, primarily through energy consumption, transportation, and industry. The urban heat island effect, whereby cities are significantly warmer than surrounding rural areas due to the concentration of buildings, roads, and human activity, creates additional environmental challenges.

Despite these problems, many researchers and policymakers believe that cities can be a key part of the solution to global challenges. Smart urban planning, investment in public transportation, and the development of green spaces can make cities more sustainable and liveable. Some researchers argue that compact, well-planned cities are actually more environmentally efficient per capita than sprawling suburban or rural lifestyles.""",
                "questions": [
                    {"number": 8, "question_type": "TFNG", "content": "By 2030, approximately 5 billion people are expected to live in urban areas.", "correct_answer": "TRUE"},
                    {"number": 9, "question_type": "TFNG", "content": "Rural areas typically have higher productivity per worker than urban areas.", "correct_answer": "FALSE"},
                    {"number": 10, "question_type": "TFNG", "content": "Approximately two billion people currently live in slums.", "correct_answer": "FALSE", "explanation": "The passage says around one billion."},
                    {"number": 11, "question_type": "MCQ", "content": "What percentage of global CO₂ emissions do urban areas account for?", "correct_answer": "C",
                     "choices": [{"option": "A", "text": "50%"}, {"option": "B", "text": "60%"}, {"option": "C", "text": "70%"}, {"option": "D", "text": "80%"}]},
                    {"number": 12, "question_type": "GAP", "content": "The ___ effect makes cities significantly warmer than surrounding rural areas.", "correct_answer": "URBAN HEAT ISLAND"},
                    {"number": 13, "question_type": "YNNG", "content": "The author believes cities cannot contribute to solving global environmental challenges.", "correct_answer": "NO"},
                ],
            },
            {
                "passage_number": 3,
                "title": "The Silk Road: Commerce and Culture",
                "content": """The Silk Road was not a single road, but rather a network of trade routes that connected China with Central Asia, the Middle East, and Europe for over a millennium. Named by German geographer Ferdinand von Richthofen in 1877, these routes served as conduits not only for silk and other luxury goods, but also for the exchange of ideas, religions, technologies, and diseases.

The routes were most active from around the 2nd century BCE, during the Han Dynasty, until the 15th century CE. At its height, the Silk Road stretched over 6,400 kilometres and linked dozens of major cities and civilisations. The name is somewhat misleading, as the routes carried many goods besides silk — including spices, glass, precious metals, textiles, and livestock.

Trade along the Silk Road was rarely conducted by single merchants making the entire journey from China to Rome. Instead, goods typically changed hands many times as they were traded between caravans and merchants at different stages of the journey. The Sogdians, an Iranian people who lived in the region around present-day Uzbekistan and Tajikistan, were particularly important intermediaries in this trade network.

The cultural exchange facilitated by the Silk Road was equally significant. Buddhism spread from India to China and other parts of East Asia along these routes. Islam later followed a similar path. Technologies such as papermaking, printing, gunpowder, and the compass travelled from China to the West, while glassmaking techniques moved eastward from the Roman Empire. The Silk Road also facilitated the spread of the Black Death plague across Eurasia in the 14th century.

The decline of the Silk Road was gradual and resulted from several factors. The rise of maritime trade routes, which allowed much larger quantities of goods to be transported more cheaply by sea, made the overland routes less economically attractive. The fragmentation of the Mongol Empire, which had previously provided political stability along the routes, also contributed to the decline.""",
                "questions": [
                    {"number": 14, "question_type": "TFNG", "content": "Ferdinand von Richthofen was the first person to trade along the Silk Road.", "correct_answer": "FALSE", "explanation": "He simply named the route in 1877."},
                    {"number": 15, "question_type": "TFNG", "content": "The Silk Road was used for almost 1,700 years.", "correct_answer": "TRUE", "explanation": "From ~2nd century BCE to 15th century CE is approximately 1,700 years."},
                    {"number": 16, "question_type": "TFNG", "content": "Individual merchants typically made the entire journey from China to Rome.", "correct_answer": "FALSE"},
                    {"number": 17, "question_type": "MCQ", "content": "Which people were described as particularly important intermediaries in the Silk Road trade?", "correct_answer": "B",
                     "choices": [{"option": "A", "text": "The Han Chinese"}, {"option": "B", "text": "The Sogdians"}, {"option": "C", "text": "The Romans"}, {"option": "D", "text": "The Mongols"}]},
                    {"number": 18, "question_type": "GAP", "content": "The Black Death plague spread across Eurasia via the Silk Road in the ___ century.", "correct_answer": "14TH"},
                    {"number": 19, "question_type": "MATCH", "content": "Which technology travelled from China to the West along the Silk Road?", "correct_answer": "PAPERMAKING"},
                    {"number": 20, "question_type": "SHORT", "content": "What type of trade routes replaced the Silk Road as the more economically efficient option?", "correct_answer": "MARITIME"},
                ],
            },
        ],
    },

    # ──────────────────────────────────────────────────────────────────────
    # PRACTICE PASSAGES (standalone)
    # ──────────────────────────────────────────────────────────────────────
    {
        "title": "Practice: The History of Writing",
        "content": """Writing is one of humanity's most important inventions. The earliest writing systems were developed independently in several different parts of the world. The Sumerians of ancient Mesopotamia developed cuneiform script around 3400 BCE, primarily for the purpose of recording economic transactions and administrative information. Shortly afterwards, the ancient Egyptians developed their own writing system known as hieroglyphics.

Early writing systems were logographic — each symbol represented a word or concept rather than a sound. This made them very complex, as thousands of symbols were needed to write even basic texts. Over time, most writing systems evolved to become phonetic, with symbols representing sounds rather than meanings. This greatly reduced the number of symbols required and made literacy more accessible.

The Phoenician alphabet, developed around 1050 BCE, was one of the most influential writing systems in history. Unlike earlier scripts, it consisted of only 22 letters, each representing a consonant sound. Greek traders adapted the Phoenician alphabet around 800 BCE, adding vowel sounds to create the first true alphabet. Latin, which evolved from Greek, became the basis for most modern European writing systems.

The invention of the printing press by Johannes Gutenberg around 1440 CE revolutionised the production and distribution of written material. Before the printing press, books had to be copied by hand, making them extremely expensive and rare. Gutenberg's press made it possible to produce hundreds of copies of a text quickly and cheaply, contributing to the spread of literacy and the Renaissance and Reformation movements.""",
        "passage_number": 1,
        "is_standalone": True,
        "difficulty": "EASY",
        "is_premium": False,
        "questions": [
            {"number": 1, "question_type": "TFNG", "content": "The Sumerians developed writing primarily to record agricultural information.", "correct_answer": "FALSE", "explanation": "The passage says it was for economic transactions and administrative information."},
            {"number": 2, "question_type": "TFNG", "content": "Logographic writing systems required thousands of symbols.", "correct_answer": "TRUE"},
            {"number": 3, "question_type": "TFNG", "content": "The Phoenician alphabet had 26 letters.", "correct_answer": "FALSE", "explanation": "It had 22 letters."},
            {"number": 4, "question_type": "MCQ", "content": "Which civilisation added vowel sounds to the Phoenician alphabet?", "correct_answer": "B",
             "choices": [{"option": "A", "text": "Romans"}, {"option": "B", "text": "Greeks"}, {"option": "C", "text": "Egyptians"}, {"option": "D", "text": "Phoenicians"}]},
            {"number": 5, "question_type": "GAP", "content": "Gutenberg invented the printing press around ___ CE.", "correct_answer": "1440"},
            {"number": 6, "question_type": "SHORT", "content": "What was the name of the ancient Egyptian writing system?", "correct_answer": "HIEROGLYPHICS"},
        ],
    },
    {
        "title": "Practice: Renewable Energy Transition",
        "content": """The transition from fossil fuels to renewable energy sources represents one of the most significant shifts in the global economy in modern history. This transformation is being driven by multiple factors: the declining cost of renewable technologies, growing concerns about climate change, and the finite nature of fossil fuel reserves.

Wind power has experienced remarkable growth over the past two decades. The global installed capacity of wind power has grown from approximately 7,500 megawatts in 1997 to over 700,000 megawatts in 2020 — representing a nearly hundred-fold increase. Modern wind turbines are highly efficient machines capable of converting up to 45% of the kinetic energy of the wind into electricity, compared to efficiency rates of around 15% in the early days of the technology.

Hydroelectric power remains the world's largest source of renewable electricity, accounting for approximately 16% of global electricity production. Hydroelectric plants have very long operational lifetimes, often exceeding 50 years, and produce electricity with no direct carbon emissions. However, large hydroelectric dams can have significant environmental and social impacts, including the displacement of local communities and the disruption of river ecosystems.

Battery storage technology is becoming increasingly important as the proportion of variable renewable energy in electricity grids grows. Solar and wind power only generate electricity when the sun is shining or wind is blowing, creating challenges for grid operators. Advances in lithium-ion battery technology have dramatically reduced the cost of energy storage, helping to address this intermittency problem and enabling greater penetration of renewable energy.""",
        "passage_number": 2,
        "is_standalone": True,
        "difficulty": "MEDIUM",
        "is_premium": False,
        "questions": [
            {"number": 1, "question_type": "TFNG", "content": "Global wind power capacity grew approximately 100 times between 1997 and 2020.", "correct_answer": "TRUE"},
            {"number": 2, "question_type": "TFNG", "content": "Modern wind turbines can convert up to 60% of wind energy into electricity.", "correct_answer": "FALSE", "explanation": "The passage states up to 45%."},
            {"number": 3, "question_type": "MCQ", "content": "What percentage of global electricity does hydroelectric power account for?", "correct_answer": "A",
             "choices": [{"option": "A", "text": "16%"}, {"option": "B", "text": "20%"}, {"option": "C", "text": "25%"}, {"option": "D", "text": "30%"}]},
            {"number": 4, "question_type": "TFNG", "content": "Large hydroelectric dams have no negative environmental impacts.", "correct_answer": "FALSE"},
            {"number": 5, "question_type": "GAP", "content": "Battery storage technology uses ___ batteries to store renewable energy.", "correct_answer": "LITHIUM-ION"},
            {"number": 6, "question_type": "SHORT", "content": "What challenge does intermittency create for electricity grid operators?", "correct_answer": "MANAGING VARIABLE SUPPLY"},
        ],
    },
]


LISTENING_TESTS = [
    {
        "title": "Listening Practice: University Enrolment",
        "section_number": 1,
        "is_standalone": True,
        "difficulty": "EASY",
        "is_premium": False,
        "transcript": "A student is calling the university admissions office to enquire about course enrolment.",
        "questions": [
            {"number": 1, "question_type": "GAP", "content": "The student's last name is ___.", "correct_answer": "MORRISON"},
            {"number": 2, "question_type": "GAP", "content": "The student's student ID number is ___.", "correct_answer": "S4729"},
            {"number": 3, "question_type": "MCQ", "content": "Which course does the student want to enrol in?", "correct_answer": "B",
             "choices": [{"option": "A", "text": "Business Administration"}, {"option": "B", "text": "Computer Science"}, {"option": "C", "text": "Electrical Engineering"}]},
            {"number": 4, "question_type": "GAP", "content": "The registration deadline is the ___ of March.", "correct_answer": "15TH"},
            {"number": 5, "question_type": "GAP", "content": "The registration fee is ___ pounds.", "correct_answer": "250"},
            {"number": 6, "question_type": "MCQ", "content": "How can the student pay the registration fee?", "correct_answer": "A",
             "choices": [{"option": "A", "text": "Online bank transfer"}, {"option": "B", "text": "Cash at the office"}, {"option": "C", "text": "Cheque only"}]},
            {"number": 7, "question_type": "GAP", "content": "The student should bring their ___ to the orientation day.", "correct_answer": "PASSPORT"},
            {"number": 8, "question_type": "GAP", "content": "The orientation is held in building ___.", "correct_answer": "D"},
            {"number": 9, "question_type": "MCQ", "content": "When is the orientation held?", "correct_answer": "C",
             "choices": [{"option": "A", "text": "Monday morning"}, {"option": "B", "text": "Tuesday afternoon"}, {"option": "C", "text": "Wednesday morning"}]},
            {"number": 10, "question_type": "GAP", "content": "The student is advised to arrive ___ minutes early.", "correct_answer": "15"},
        ],
    },
    {
        "title": "Listening Practice: Museum Tour",
        "section_number": 2,
        "is_standalone": True,
        "difficulty": "EASY",
        "is_premium": False,
        "transcript": "A museum guide is giving a tour to a group of visitors.",
        "questions": [
            {"number": 1, "question_type": "GAP", "content": "The museum was founded in ___.", "correct_answer": "1897"},
            {"number": 2, "question_type": "GAP", "content": "The natural history collection is located on the ___ floor.", "correct_answer": "SECOND"},
            {"number": 3, "question_type": "MCQ", "content": "How many permanent exhibitions does the museum have?", "correct_answer": "B",
             "choices": [{"option": "A", "text": "5"}, {"option": "B", "text": "8"}, {"option": "C", "text": "12"}]},
            {"number": 4, "question_type": "MATCH", "content": "The special exhibition on ancient Egypt closes in which month?", "correct_answer": "NOVEMBER"},
            {"number": 5, "question_type": "GAP", "content": "The museum café is open until ___ pm.", "correct_answer": "5:30"},
            {"number": 6, "question_type": "MCQ", "content": "Which item is NOT permitted inside the museum?", "correct_answer": "A",
             "choices": [{"option": "A", "text": "Large bags"}, {"option": "B", "text": "Cameras"}, {"option": "C", "text": "Notebooks"}]},
            {"number": 7, "question_type": "GAP", "content": "Audio guides are available for ___ pounds.", "correct_answer": "3"},
            {"number": 8, "question_type": "GAP", "content": "The children's workshop starts at ___ o'clock.", "correct_answer": "2"},
        ],
    },
    {
        "title": "Listening Practice: Job Interview Preparation",
        "section_number": 3,
        "is_standalone": True,
        "difficulty": "MEDIUM",
        "is_premium": False,
        "transcript": "A career counsellor is advising a university student on job interview preparation.",
        "questions": [
            {"number": 1, "question_type": "MCQ", "content": "What does the counsellor say is the most important preparation step?", "correct_answer": "B",
             "choices": [{"option": "A", "text": "Preparing your CV"}, {"option": "B", "text": "Researching the company"}, {"option": "C", "text": "Choosing appropriate clothing"}]},
            {"number": 2, "question_type": "GAP", "content": "Candidates should arrive at least ___ minutes before the interview.", "correct_answer": "10"},
            {"number": 3, "question_type": "GAP", "content": "The counsellor recommends preparing answers to at least ___ common questions.", "correct_answer": "20"},
            {"number": 4, "question_type": "MCQ", "content": "What does the counsellor say about salary negotiation?", "correct_answer": "C",
             "choices": [{"option": "A", "text": "Never discuss salary"}, {"option": "B", "text": "Discuss salary immediately"}, {"option": "C", "text": "Wait until a job offer is made"}]},
            {"number": 5, "question_type": "GAP", "content": "Following up after an interview should be done within ___ hours.", "correct_answer": "24"},
            {"number": 6, "question_type": "MCQ", "content": "What type of questions does the counsellor recommend asking the interviewer?", "correct_answer": "A",
             "choices": [{"option": "A", "text": "Questions about company culture and growth"}, {"option": "B", "text": "Questions about salary and benefits"}, {"option": "C", "text": "Questions about working hours"}]},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed sample IELTS Reading and Listening test data'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing IELTS data before seeding')

    def handle(self, *args, **options):
        from ielts.models import (
            IELTSTest, ReadingPassage, ReadingQuestion, ReadingChoice,
            ListeningSection, ListeningQuestion, ListeningChoice,
        )

        if options['clear']:
            self.stdout.write('Clearing existing IELTS data...')
            ReadingPassage.objects.all().delete()
            ListeningSection.objects.all().delete()
            IELTSTest.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('Cleared.'))

        reading_count = 0
        listening_count = 0

        with transaction.atomic():
            # ── Reading ──────────────────────────────────────────────────
            for test_data in READING_TESTS:
                if 'parts' in test_data:
                    # Full mock test
                    test, created = IELTSTest.objects.get_or_create(
                        title=test_data['title'],
                        defaults={
                            'test_type': test_data.get('test_type', 'FULL_MOCK'),
                            'is_premium': test_data.get('is_premium', False),
                        }
                    )
                    if not created:
                        self.stdout.write(f'  Skipping existing: {test_data["title"]}')
                        continue
                    for part in test_data['parts']:
                        passage = ReadingPassage.objects.create(
                            test=test,
                            title=part['title'],
                            content=part['content'],
                            passage_number=part['passage_number'],
                            time_limit=20,
                            difficulty=test_data.get('difficulty', 'MEDIUM'),
                            is_standalone=False,
                            is_premium=test_data.get('is_premium', False),
                        )
                        for q in part.get('questions', []):
                            question = ReadingQuestion.objects.create(
                                passage=passage,
                                number=q['number'],
                                question_type=q.get('question_type', 'MCQ'),
                                content=q['content'],
                                correct_answer=q.get('correct_answer', ''),
                                explanation=q.get('explanation', ''),
                            )
                            for c in q.get('choices', []):
                                ReadingChoice.objects.create(question=question, option=c['option'], text=c['text'])
                    reading_count += 1
                    self.stdout.write(f'  ✓ Mock test: {test_data["title"]} ({len(test_data["parts"])} parts)')
                else:
                    # Single passage
                    _, created = ReadingPassage.objects.get_or_create(
                        title=test_data['title'],
                        defaults={
                            'content': test_data['content'],
                            'passage_number': test_data.get('passage_number', 1),
                            'time_limit': 20,
                            'difficulty': test_data.get('difficulty', 'MEDIUM'),
                            'is_standalone': True,
                            'is_premium': test_data.get('is_premium', False),
                        }
                    )
                    if not created:
                        self.stdout.write(f'  Skipping existing: {test_data["title"]}')
                        continue
                    passage = ReadingPassage.objects.get(title=test_data['title'])
                    for q in test_data.get('questions', []):
                        question = ReadingQuestion.objects.create(
                            passage=passage,
                            number=q['number'],
                            question_type=q.get('question_type', 'MCQ'),
                            content=q['content'],
                            correct_answer=q.get('correct_answer', ''),
                            explanation=q.get('explanation', ''),
                        )
                        for c in q.get('choices', []):
                            ReadingChoice.objects.create(question=question, option=c['option'], text=c['text'])
                    reading_count += 1
                    self.stdout.write(f'  ✓ Practice passage: {test_data["title"]}')

            # ── Listening ────────────────────────────────────────────────
            for sec_data in LISTENING_TESTS:
                _, created = ListeningSection.objects.get_or_create(
                    title=sec_data['title'],
                    defaults={
                        'section_number': sec_data.get('section_number', 1),
                        'transcript': sec_data.get('transcript', ''),
                        'difficulty': sec_data.get('difficulty', 'MEDIUM'),
                        'is_standalone': True,
                        'is_premium': sec_data.get('is_premium', False),
                    }
                )
                if not created:
                    self.stdout.write(f'  Skipping existing: {sec_data["title"]}')
                    continue
                section = ListeningSection.objects.get(title=sec_data['title'])
                for q in sec_data.get('questions', []):
                    question = ListeningQuestion.objects.create(
                        section=section,
                        number=q['number'],
                        question_type=q.get('question_type', 'GAP'),
                        content=q['content'],
                        correct_answer=q.get('correct_answer', ''),
                        explanation=q.get('explanation', ''),
                    )
                    for c in q.get('choices', []):
                        ListeningChoice.objects.create(question=question, option=c['option'], text=c['text'])
                listening_count += 1
                self.stdout.write(f'  ✓ Listening section: {sec_data["title"]}')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Added {reading_count} reading tests and {listening_count} listening sections.'
        ))
