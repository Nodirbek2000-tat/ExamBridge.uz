"""
Seed real IELTS Academic Reading tests with correct questions and answers.
Source: ielts-mentor.com (Tests 117, 118, 119)

Usage:
    python manage.py seed_reading
    python manage.py seed_reading --clear-only
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from ielts.models import IELTSTest, ReadingPassage, ReadingQuestion, ReadingChoice

# ─────────────────────────────────────────────────────────────────────────────
# TEST DATA
# ─────────────────────────────────────────────────────────────────────────────

TESTS = [

# ══════════════════════════════════════════════════════════════════════════════
# TEST 119
# ══════════════════════════════════════════════════════════════════════════════
{
    'title': 'Cambridge IELTS — Test 119',
    'difficulty': 'HARD',
    'is_premium': False,
    'passages': [
        # ── Passage 1 ─────────────────────────────────────────────────────────
        {
            'title': 'Nutmeg — A Valuable Spice',
            'passage_number': 1,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """Nutmeg — A Valuable Spice

The nutmeg tree (Myristica fragrans) is a large evergreen native to Southeast Asia, historically grown only in the Banda Sea islands of Indonesia's Moluccas. The tree features dark green oval leaves and produces yellow bell-shaped flowers with pale yellow pear-shaped fruits. When ripe, the fruit's flesh husk splits, revealing a purple-brown shiny seed surrounded by a lacy red or crimson covering called an 'aril'. Nutmeg comes from the dried seed; mace derives from the aril.

In medieval Europe, nutmeg was highly valued as flavouring, medicine, and preservative. Arabs controlled imports to Venice at premium prices, concealing the source until Portuguese traders reached the Banda Islands in 1512. Dutch merchants eventually dominated the spice trade, establishing the VOC (Dutch East India Company) in 1602, which became the world's richest commercial operation by 1617.

The plague's devastation created enormous nutmeg demand — it sold for 68,000 times its Indonesian cost in London. The Dutch monopolised production through strict controls: destroying trees outside designated zones, punishing seed cultivation, and coating exports with lime to prevent propagation elsewhere.

The British held the island of Run until the 1667 Treaty of Breda, when the Dutch exchanged it for Manhattan. The monopoly eventually collapsed through three events: French smuggling to Mauritius in 1770, an 1778 volcanic tsunami destroying half the Banda groves, and British seizure and redistribution of seedlings in 1809.

Today, nutmeg production spans multiple countries, averaging 10,000–12,000 tonnes annually.""",
            'questions': [
                {'number': 1,  'question_type': 'GAP',  'content': 'Complete the notes. Write ONE WORD ONLY.\n\nNutmeg Tree — Physical Features:\nLeaf shape: ___________', 'correct_answer': 'oval'},
                {'number': 2,  'question_type': 'GAP',  'content': 'Structure that surrounds the ripe fruit before it splits: ___________', 'correct_answer': 'husk'},
                {'number': 3,  'question_type': 'GAP',  'content': 'Part of the fruit that provides the spice nutmeg: ___________', 'correct_answer': 'seed'},
                {'number': 4,  'question_type': 'GAP',  'content': 'Spice derived from the aril: ___________', 'correct_answer': 'mace'},
                {'number': 5,  'question_type': 'TFNG', 'content': 'Most Europeans in medieval times knew where nutmeg was grown.', 'correct_answer': 'FALSE'},
                {'number': 6,  'question_type': 'TFNG', 'content': 'The VOC was the world\'s first major trading company.', 'correct_answer': 'NOT GIVEN'},
                {'number': 7,  'question_type': 'TFNG', 'content': 'After the Treaty of Breda, the Dutch controlled all nutmeg-producing islands.', 'correct_answer': 'TRUE'},
                {'number': 8,  'question_type': 'GAP',  'content': 'Complete the table. Write ONE WORD ONLY.\n\nHistory of the Nutmeg Trade:\nMedieval traders who controlled imports to Europe: ___________', 'correct_answer': 'Arabs'},
                {'number': 9,  'question_type': 'GAP',  'content': 'Disease that greatly increased demand for nutmeg: ___________', 'correct_answer': 'plague'},
                {'number': 10, 'question_type': 'GAP',  'content': 'Substance the Dutch coated exported nutmeg seeds with: ___________', 'correct_answer': 'lime'},
                {'number': 11, 'question_type': 'GAP',  'content': 'Island under British control until 1667: ___________', 'correct_answer': 'Run'},
                {'number': 12, 'question_type': 'GAP',  'content': 'Location where the French smuggled nutmeg plants in 1770: ___________', 'correct_answer': 'Mauritius'},
                {'number': 13, 'question_type': 'GAP',  'content': 'Natural disaster that destroyed half the Banda groves in 1778: ___________', 'correct_answer': 'tsunami'},
            ],
        },

        # ── Passage 2 ─────────────────────────────────────────────────────────
        {
            'title': 'Driverless Cars',
            'passage_number': 2,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """Driverless Cars

A. The automotive sector is well used to adapting to automation in manufacturing. The implementation of robotic car manufacture from the 1970s onwards led to significant cost savings and improvements in the reliability and flexibility of vehicle mass production. A new challenge to vehicle production is now on the horizon and, again, it comes from automation. However, this time it is not to do with the manufacturing process, but with the vehicles themselves. Research projects on vehicle automation are not new. Vehicles with limited self-driving capabilities have been around for more than 50 years, resulting in significant contributions towards driver assistance systems. But since Google announced in 2010 that it had been trialling self-driving cars on the streets of California, progress in this field has quickly gathered pace.

B. There are many reasons why technology is advancing so fast. One frequently cited motive is safety; indeed, research at the UK's Transport Research Laboratory has demonstrated that "more than 90 percent of road collisions involve human error as a contributory factor," and it is the primary cause in the vast majority. Automation may help to reduce the incidence of this. Another aim is to free the time people spend driving for other purposes. If the vehicle can do some or all of the driving, it may be possible to be productive, to socialise or simply to relax while automation systems have responsibility for safe control of the vehicle. If the vehicle can do the driving, those who are challenged by existing mobility models — such as older or disabled travellers — may be able to enjoy significantly greater travel autonomy.

C. Beyond these direct benefits, we can consider the wider implications for transport and society, and how manufacturing processes might need to respond as a result. At present, "the average car spends more than 90 percent of its life parked." Automation means that initiatives for car-sharing become much more viable, particularly in urban areas with significant travel demand. If a significant proportion of the population choose to use shared automated vehicles, mobility demand can be met by far fewer vehicles.

D. The Massachusetts Institute of Technology investigated automated mobility in Singapore, finding that fewer than 30 percent of the vehicles currently used would be required if fully automated car sharing could be implemented. If this is the case, it might mean that we need to manufacture far fewer vehicles to meet demand. However, the number of trips being taken would probably increase, partly because empty vehicles would have to be moved from one customer to the next. Modelling work by the University of Michigan Transportation Research Institute suggests "automated vehicles might reduce vehicle ownership by 43 percent, but that vehicles' average annual mileage double as a result." As a consequence, each vehicle would be used more intensively, and might need replacing sooner. This faster rate of turnover may mean that vehicle production will not necessarily decrease.

E. Automation may prompt other changes in vehicle manufacture. If we move to a model where consumers are tending not to own a single vehicle but to purchase access to a range of vehicles through a mobility provider, drivers will have the freedom to select one that best suits their needs for a particular journey. Since, for most of the time, most of the seats in most cars are unoccupied, this may boost production of a smaller, more efficient range of vehicles that suit the needs of individuals. Specialised vehicles may then be available for exceptional journeys, such as going on a family camping trip or helping a son or daughter move to university.

F. There are a number of hurdles to overcome in delivering automated vehicles to our roads. These include the technical difficulties in ensuring that the vehicle works reliably in the infinite range of traffic, weather and road situations it might encounter; the regulatory challenges in understanding how liability and enforcement might change when drivers are no longer essential for vehicle operation; and the societal changes that may be required for communities to trust and accept automated vehicles as being a valuable part of the mobility landscape.

G. It's clear that there are many challenges that need to be addressed but, through robust and targeted research, these can most probably be conquered within the next 10 years. Mobility will change in such potentially significant ways and in association with so many other technological developments, such as telepresence and virtual reality, that it is hard to make concrete predictions about the future. However, one thing is certain: change is coming, and the need to be flexible in response to this will be vital for those involved in manufacturing the vehicles that will deliver future mobility.""",
            'questions': [
                # Q14-18: Section matching (MATCH — answer is the section letter)
                {'number': 14, 'question_type': 'MATCH', 'content': 'The reading passage has seven sections A–G.\nWhich section contains the following information?\n\nA reference to the amount of time when a car is not in use.', 'correct_answer': 'C'},
                {'number': 15, 'question_type': 'MATCH', 'content': 'A mention of several advantages of driverless vehicles for individual road-users.', 'correct_answer': 'B'},
                {'number': 16, 'question_type': 'MATCH', 'content': 'A reference to the opportunity of choosing the most appropriate vehicle for each trip.', 'correct_answer': 'E'},
                {'number': 17, 'question_type': 'MATCH', 'content': 'An estimate of how long it will take to overcome a number of problems.', 'correct_answer': 'G'},
                {'number': 18, 'question_type': 'MATCH', 'content': 'A suggestion that the use of driverless cars may have no effect on the number of vehicles manufactured.', 'correct_answer': 'D'},
                # Q19-22: Summary completion (GAP)
                {'number': 19, 'question_type': 'GAP', 'content': 'Complete the summary. Write NO MORE THAN TWO WORDS from the passage.\n\nFigures from the Transport Research Laboratory indicate that most motor accidents are partly due to ___________, so the introduction of driverless vehicles will result in greater safety.', 'correct_answer': 'human error'},
                {'number': 20, 'question_type': 'GAP', 'content': 'Complete the summary. Write NO MORE THAN TWO WORDS.\n\nSchemes for ___________ will be more workable, especially in towns and cities, resulting in fewer cars on the road.', 'correct_answer': 'car-sharing'},
                {'number': 21, 'question_type': 'GAP', 'content': 'Complete the summary. Write ONE WORD ONLY.\n\nAccording to the University of Michigan, there could be a 43 percent drop in ___________ of cars.', 'correct_answer': 'ownership'},
                {'number': 22, 'question_type': 'GAP', 'content': 'Complete the summary. Write ONE WORD ONLY.\n\nThe yearly ___________ of each car would be twice as high on average. This would lead to higher turnover of vehicles.', 'correct_answer': 'mileage'},
                # Q23-26: Multiple choice
                {
                    'number': 23, 'question_type': 'MCQ',
                    'content': 'Which benefit of automated vehicles does the writer mention? (Question 23 of 2)',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'Car travellers could enjoy considerable cost savings.'),
                        ('B', 'It would be easier to find parking spaces in urban areas.'),
                        ('C', 'Travellers could spend journeys doing something other than driving.'),
                        ('D', 'People who find driving physically difficult could travel independently.'),
                        ('E', 'A reduction in the number of cars would mean a reduction in pollution.'),
                    ],
                },
                {
                    'number': 24, 'question_type': 'MCQ',
                    'content': 'Which benefit of automated vehicles does the writer mention? (Question 24 of 2)',
                    'correct_answer': 'D',
                    'choices': [
                        ('A', 'Car travellers could enjoy considerable cost savings.'),
                        ('B', 'It would be easier to find parking spaces in urban areas.'),
                        ('C', 'Travellers could spend journeys doing something other than driving.'),
                        ('D', 'People who find driving physically difficult could travel independently.'),
                        ('E', 'A reduction in the number of cars would mean a reduction in pollution.'),
                    ],
                },
                {
                    'number': 25, 'question_type': 'MCQ',
                    'content': 'Which challenge to automated vehicle development does the writer mention? (Question 25 of 2)',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', 'Making sure the general public has confidence in automated vehicles.'),
                        ('B', 'Managing the pace of transition from conventional to automated vehicles.'),
                        ('C', 'Deciding how to compensate professional drivers who become redundant.'),
                        ('D', 'Setting up the infrastructure to make roads suitable for automated vehicles.'),
                        ('E', 'Getting automated vehicles to adapt to various different driving conditions.'),
                    ],
                },
                {
                    'number': 26, 'question_type': 'MCQ',
                    'content': 'Which challenge to automated vehicle development does the writer mention? (Question 26 of 2)',
                    'correct_answer': 'E',
                    'choices': [
                        ('A', 'Making sure the general public has confidence in automated vehicles.'),
                        ('B', 'Managing the pace of transition from conventional to automated vehicles.'),
                        ('C', 'Deciding how to compensate professional drivers who become redundant.'),
                        ('D', 'Setting up the infrastructure to make roads suitable for automated vehicles.'),
                        ('E', 'Getting automated vehicles to adapt to various different driving conditions.'),
                    ],
                },
            ],
        },

        # ── Passage 3 ─────────────────────────────────────────────────────────
        {
            'title': 'What is Exploration?',
            'passage_number': 3,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """What is Exploration?

We are all explorers. Our desire to discover, and then share that new-found knowledge, is part of what makes us human — indeed, this has played an important part in our success as a species. Long before the first caveman slumped down beside the fire and grunted news that there were plenty of wildebeest over yonder, our ancestors had learnt the value of sending out scouts to investigate the unknown. This questing nature of ours undoubtedly helped our species spread around the globe, just as it nowadays no doubt helps the last nomadic Penan maintain their existence in the depleted forests of Borneo, and a visitor negotiate the subways of New York.

Over the years, we've come to think of explorers as a peculiar breed — different from the rest of us, different from those of us who are merely 'well travelled', even; and perhaps there is a type of person more suited to seeking out the new, a type of caveman more inclined to risk venturing out. That, however, doesn't take away from the fact that we all have this enquiring instinct, even today; and that in all sorts of professions — whether artist, marine biologist or astronomer — borders of the unknown are being tested each day.

Thomas Hardy set some of his novels in Egdon Heath, a fictional area of uncultivated land, and used the landscape to suggest the desires and fears of his characters. He is delving into matters we all recognise because they are common to humanity. This is surely an act of exploration, and into a world as remote as the author chooses. Explorer and travel writer Peter Fleming talks of the moment when the explorer returns to the existence he has left behind with his loved ones. The traveller 'who has for weeks or months seen himself only as a puny and irrelevant alien crawling laboriously over a country in which he has no roots and no background, suddenly encounters his other self, a relatively solid figure, with a place in the minds of certain people'.

In this book about the exploration of the earth's surface, I have confined myself to those whose travels were real and who also aimed at more than personal discovery. But that still left me with another problem: the word 'explorer' has become associated with a past era. We think back to a golden age, as if exploration peaked somehow in the 19th century — as if the process of discovery is now on the decline, though the truth is that we have named only one and a half million of this planet's species, and there may be more than 10 million — and that's not including bacteria.

Here is how some of today's 'explorers' define the word. Ran Fiennes, dubbed the 'greatest living explorer', said, 'An explorer is someone who has done something that no human has done before — and also done something scientifically useful.' Chris Bonington, a leading mountaineer, felt exploration was to be found in the act of physically touching the unknown: 'You have to have gone somewhere new.' Then Robin Hanbury-Tenison, a campaigner on behalf of remote so-called 'tribal' peoples, said, 'A traveller simply records information about some far-off world, and reports back; but an explorer changes the world.' Wilfred Thesiger, who crossed Arabia's Empty Quarter in 1946, told me, 'If I'd gone across by camel when I could have gone by car, it would have been a stunt.' To him, exploration meant bringing back information from a remote place regardless of any great self-discovery.

Each definition is slightly different — and tends to reflect the field of endeavour of each pioneer. It was the same whoever I asked: the prominent historian would say exploration was a thing of the past, the cutting-edge scientist would say it was of the present. They each set their own particular criteria; the common factor being that they all had, unlike many of us who simply enjoy travel, both a very definite objective from the outset and also a desire to record their findings.

I'd best declare my own bias. As a writer, I'm interested in the exploration of ideas. I've done a great many expeditions and each one was unique. I've lived for months alone with isolated groups of people all around the world, even two 'uncontacted tribes'. But none of these things is of the slightest interest to anyone unless, through my books, I've found a new slant, explored a new idea. The time has long passed for the great continental voyages — another walk to the poles, another crossing of the Empty Quarter. However, this is to disregard the role the human mind has in conveying remote places; and this is what interests me: how a fresh interpretation, even of a well-travelled route, can give its readers new insights.""",
            'questions': [
                # Q27-32: MCQ
                {
                    'number': 27, 'question_type': 'MCQ',
                    'content': 'The writer refers to visitors to New York to illustrate the point that',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', 'exploration is an intrinsic element of being human.'),
                        ('B', 'most people are enthusiastic about exploring.'),
                        ('C', 'exploration can lead to surprising results.'),
                        ('D', 'most people find exploration daunting.'),
                    ],
                },
                {
                    'number': 28, 'question_type': 'MCQ',
                    'content': 'According to the second paragraph, what is the writer\'s view of explorers?',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'Their discoveries have brought both benefits and disadvantages.'),
                        ('B', 'Their main value is in teaching others.'),
                        ('C', 'They act on an urge that is common to everyone.'),
                        ('D', 'They tend to be more attracted to certain professions than to others.'),
                    ],
                },
                {
                    'number': 29, 'question_type': 'MCQ',
                    'content': 'The writer refers to a description of Egdon Heath to suggest that',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'Hardy was writing about his own experience of exploration.'),
                        ('B', 'Hardy was mistaken about the nature of exploration.'),
                        ('C', "Hardy's aim was to investigate people's emotional states."),
                        ('D', "Hardy's aim was to show the attraction of isolation."),
                    ],
                },
                {
                    'number': 30, 'question_type': 'MCQ',
                    'content': 'In the fourth paragraph, the writer refers to \'a golden age\' to suggest that',
                    'correct_answer': 'D',
                    'choices': [
                        ('A', 'the amount of useful information produced by exploration has decreased.'),
                        ('B', 'fewer people are interested in exploring than in the 19th century.'),
                        ('C', 'recent developments have made exploration less exciting.'),
                        ('D', 'we are wrong to think that exploration is no longer necessary.'),
                    ],
                },
                {
                    'number': 31, 'question_type': 'MCQ',
                    'content': 'In the sixth paragraph, when discussing the definition of exploration, the writer argues that',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', "people tend to relate exploration to their own professional interests."),
                        ('B', 'certain people are likely to misunderstand the nature of exploration.'),
                        ('C', 'the generally accepted definition has changed over time.'),
                        ('D', 'historians and scientists have more valid definitions than the general public.'),
                    ],
                },
                {
                    'number': 32, 'question_type': 'MCQ',
                    'content': 'In the last paragraph, the writer explains that he is interested in',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', "how someone's personality is reflected in their choice of places to visit."),
                        ('B', 'the human ability to cast new light on places that may be familiar.'),
                        ('C', 'how travel writing has evolved to meet changing demands.'),
                        ('D', 'the feelings that writers develop about the places that they explore.'),
                    ],
                },
                # Q33-37: Match explorer to statement (MATCH — answer is letter A-E)
                {'number': 33, 'question_type': 'MATCH', 'content': 'Match each statement to the correct person.\n\nA = Peter Fleming\nB = Ran Fiennes\nC = Chris Bonington\nD = Robin Hanbury-Tenison\nE = Wilfred Thesiger\n\nHe referred to the relevance of the form of transport used.', 'correct_answer': 'E'},
                {'number': 34, 'question_type': 'MATCH', 'content': 'He described feelings on coming back home after a long journey.\n\nA = Peter Fleming  B = Ran Fiennes  C = Chris Bonington  D = Robin Hanbury-Tenison  E = Wilfred Thesiger', 'correct_answer': 'A'},
                {'number': 35, 'question_type': 'MATCH', 'content': 'He worked for the benefit of specific groups of people.\n\nA = Peter Fleming  B = Ran Fiennes  C = Chris Bonington  D = Robin Hanbury-Tenison  E = Wilfred Thesiger', 'correct_answer': 'D'},
                {'number': 36, 'question_type': 'MATCH', 'content': 'He did not consider learning about oneself an essential part of exploration.\n\nA = Peter Fleming  B = Ran Fiennes  C = Chris Bonington  D = Robin Hanbury-Tenison  E = Wilfred Thesiger', 'correct_answer': 'E'},
                {'number': 37, 'question_type': 'MATCH', 'content': 'He defined exploration as being both unique and of value to others.\n\nA = Peter Fleming  B = Ran Fiennes  C = Chris Bonington  D = Robin Hanbury-Tenison  E = Wilfred Thesiger', 'correct_answer': 'B'},
                # Q38-40: GAP
                {'number': 38, 'question_type': 'GAP', 'content': 'Complete the sentences. Write ONE WORD ONLY from the passage.\n\nThe writer has experience of a large number of ___________.', 'correct_answer': 'expeditions'},
                {'number': 39, 'question_type': 'GAP', 'content': 'The writer was the first stranger that certain previously ___________ people had encountered.', 'correct_answer': 'uncontacted'},
                {'number': 40, 'question_type': 'GAP', 'content': 'The writer believes there is no longer a need for major exploration of Earth\'s land ___________.', 'correct_answer': 'surface'},
            ],
        },
    ],
},


# ══════════════════════════════════════════════════════════════════════════════
# TEST 118
# ══════════════════════════════════════════════════════════════════════════════
{
    'title': 'Cambridge IELTS — Test 118',
    'difficulty': 'HARD',
    'is_premium': False,
    'passages': [
        # ── Passage 1 ─────────────────────────────────────────────────────────
        {
            'title': 'Roman Tunnels',
            'passage_number': 1,
            'difficulty': 'MEDIUM',
            'time_limit': 20,
            'content': """Roman Tunnels

The Romans, who once controlled areas of Europe, North Africa and Asia Minor, adopted construction techniques from other civilisations to build tunnels in their territories.

The Persians introduced the qanat method in the early first millennium BCE, which involved placing posts over a hill in a straight line and digging vertical shafts at regular intervals. Workers removed earth between shaft ends to create tunnels, with excavated soil removed via shafts that also provided ventilation. The water flowed through an underground canal to the surface. Some Persian qanats from 2,700 years ago remain functional today.

Romans adopted this qanat method for water-supply tunnels, spacing vertical shafts between 30–60 metres apart. Shafts featured handholds and footholds to allow workers to climb in and out, and were covered with wooden or stone lids. Romans used plumb lines — a cord with a weight attached — to ensure verticality and measure depth. The Claudius tunnel (41 CE) stretched 5.6 kilometres with shafts reaching 122 metres deep, requiring 11 years and approximately 30,000 workers.

By the 6th century BCE, the counter-excavation method emerged for tunnelling through high mountains. This required greater planning and advanced knowledge of surveying, mathematics and geometry since both tunnel ends required precise meeting at the mountain centre. Builders monitored direction using penetrating light and made corrections as needed. The Saldae aqueduct tunnel (428 metres) in Algeria experienced a significant deviation; inscriptions carved on the side of the tunnel document how teams missed each other and lateral construction corrected the error.

Romans used counter-excavation for mountain roads. The Furlo Pass Tunnel (37 metres long, 6 metres high, built 69–79 CE) in Italy still serves modern roads. Mineral extraction tunnels, like those at Dolaucothi mines in Wales for gold, required less planning since routes followed mineral veins.

Roman tunnel projects involved careful planning. Hard rock slowed progress; Romans used fire quenching — heating rock then cooling with water to crack it. A Bologna tunnel advanced 30 centimetres daily through solid rock, while the Claudius tunnel averaged 1.4 metres daily. Most tunnels bore inscriptions naming patrons and sometimes architects. The Çevlik tunnel in Turkey (1.4 kilometres), built to protect Seleuceia Pieria's harbour from flooding, displays entrance inscriptions indicating construction from 69–81 CE.""",
            'questions': [
                # Q1-6: Label diagram (GAP, one word only)
                {'number': 1,  'question_type': 'GAP', 'content': 'Label the diagram of the Persian Qanat method. Write ONE WORD ONLY.\n\nObjects placed over the hill in a straight line to mark the route: ___________', 'correct_answer': 'posts'},
                {'number': 2,  'question_type': 'GAP', 'content': 'The underground channel that supplies water for human use: ___________', 'correct_answer': 'canal'},
                {'number': 3,  'question_type': 'GAP', 'content': 'What the shafts provide for workers during excavation: ___________', 'correct_answer': 'ventilation'},
                {'number': 4,  'question_type': 'GAP', 'content': 'Label the Roman Qanat shaft diagram.\n\nObject placed over the opening of the shaft: ___________', 'correct_answer': 'lid'},
                {'number': 5,  'question_type': 'GAP', 'content': 'Object hanging at the end of the plumb line: ___________', 'correct_answer': 'weight'},
                {'number': 6,  'question_type': 'GAP', 'content': 'What footholds and handholds in the shaft help workers to do: ___________', 'correct_answer': 'climbing'},
                # Q7-10: TFNG
                {'number': 7,  'question_type': 'TFNG', 'content': 'The counter-excavation method completely replaced the qanat method in the 6th century BCE.', 'correct_answer': 'FALSE'},
                {'number': 8,  'question_type': 'TFNG', 'content': 'Only experienced builders were employed to construct tunnels using counter-excavation.', 'correct_answer': 'NOT GIVEN'},
                {'number': 9,  'question_type': 'TFNG', 'content': 'Information about the Saldae aqueduct construction problems was found in an ancient book.', 'correct_answer': 'FALSE'},
                {'number': 10, 'question_type': 'TFNG', 'content': 'The Saldae aqueduct builders\' mistake was that the two tunnel sections failed to meet.', 'correct_answer': 'TRUE'},
                # Q11-13: Short answers
                {'number': 11, 'question_type': 'SHORT', 'content': 'Answer the questions. Write NO MORE THAN TWO WORDS from the passage.\n\nWhat type of mineral were Dolaucothi mines in Wales built to extract?', 'correct_answer': 'gold'},
                {'number': 12, 'question_type': 'SHORT', 'content': 'In addition to the patron, whose name might be carved onto a tunnel?', 'correct_answer': 'architect'},
                {'number': 13, 'question_type': 'SHORT', 'content': 'What part of Seleuceia Pieria was the Çevlik tunnel built to protect?', 'correct_answer': 'harbour'},
            ],
        },

        # ── Passage 2 ─────────────────────────────────────────────────────────
        {
            'title': 'Changes in Reading Habits',
            'passage_number': 2,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """Changes in Reading Habits

Look around on your next plane trip. The iPad is the new pacifier for babies and toddlers. Younger school-aged children read stories on smartphones; older kids don't read at all, but hunch over video games. Parents and other passengers read on tablets or skim a flotilla of email and news feeds. Unbeknown to most of us, an invisible, game-changing transformation links everyone in this picture: the neuronal circuit that underlies the brain's ability to read is subtly, rapidly changing and this has implications for everyone from the pre-reading toddler to the expert adult.

As work in neurosciences indicates, the acquisition of literacy necessitated a new circuit in our species' brain more than 6,000 years ago. That circuit evolved from a very simple mechanism for decoding basic information to the present, highly elaborated reading brain. My research depicts how the present reading brain enables the development of some of our most important intellectual and affective processes: internalised knowledge, analogical reasoning, and inference; perspective-taking and empathy; critical analysis and the generation of insight. Research surfacing in many parts of the world now cautions that each of these essential 'deep reading' processes may be under threat as we move into digital-based modes of reading.

This is not a simple, binary issue of print versus digital reading and technological innovations. As MIT scholar Sherry Turkle has written, we do not err as a society when we innovate but when we ignore what we disrupt or diminish while innovating. In this hinge moment between print and digital cultures, society needs to confront what is diminishing in the expert reading circuit, what our children and older students are not developing, and what we can do about it.

We know from research that the reading circuit is not given to human beings through a genetic blueprint like vision or language; it needs an environment to develop. Further, it will adapt to that environment's requirements — from different writing systems to the characteristics of whatever medium is used. If the dominant medium advantages processes that are fast, multi-task oriented and well-suited for large volumes of information, like the current digital medium, so will the reading circuit. As UCLA psychologist Patricia Greenfield writes, the result is that less attention and time will be allocated to slower, time-demanding deep reading processes.

Increasing reports from educators and from researchers in psychology and the humanities bear this out. English literature scholar and teacher Mark Edmundson describes how many college students actively avoid the classic literature of the 19th and 20th centuries in favour of something simpler as they no longer have the patience to read longer, denser, more difficult texts. We should be less concerned with students' 'cognitive impatience', however, than by what may underlie it: the potential inability of large numbers of students to read with a level of critical analysis sufficient to comprehend the complexity of thought and argument found in more demanding texts.

Multiple studies show that digital screen use may be causing a variety of troubling downstream effects on reading comprehension in older high school and college students. In Stavanger, Norway, psychologist Anne Mangen and colleagues studied how high school students comprehend the same material in different mediums. Mangen's group asked subjects questions about a short story whose plot had universal student appeal; half of the students read the story on a tablet, the other half in paperback. Results indicated that students who read on print were superior in their comprehension to screen-reading peers, particularly in their ability to sequence detail and reconstruct the plot.

Ziming Liu from San Jose State University has conducted a series of studies which indicate that the 'new norm' in reading is skimming, involving word-spotting and browsing through the text. Many readers now use a pattern when reading in which they sample the first line and then word-spot through the rest of the text. When the reading brain skims like this, it reduces time allocated to deep reading processes, leaving us susceptible to false information and irrational ideas.

There's an old rule in neuroscience that does not alter with age: use it or lose it. It is a very hopeful principle when applied to critical thought in the reading brain because it implies choice. The story of the changing reading brain is hardly finished. We possess both the science and the technology to identify and redress the changes in how we read before they become entrenched.""",
            'questions': [
                # Q14-17: MCQ
                {
                    'number': 14, 'question_type': 'MCQ',
                    'content': 'What is the writer\'s main point in the first paragraph?',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', 'Our use of technology is having a hidden effect on us.'),
                        ('B', 'Technology can be used to help youngsters to read.'),
                        ('C', 'Travellers should be encouraged to use technology on planes.'),
                        ('D', 'Playing games is a more popular use of technology than reading.'),
                    ],
                },
                {
                    'number': 15, 'question_type': 'MCQ',
                    'content': 'What main point does Sherry Turkle make about innovation?',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', 'Technological innovation has led to a reduction in print reading.'),
                        ('B', 'We should pay attention to what might be lost when innovation occurs.'),
                        ('C', 'We should encourage more young people to become involved in innovation.'),
                        ('D', 'There is a difference between developing products and developing ideas.'),
                    ],
                },
                {
                    'number': 16, 'question_type': 'MCQ',
                    'content': 'What point is the writer making in the fourth paragraph?',
                    'correct_answer': 'D',
                    'choices': [
                        ('A', 'Humans have an inborn ability to read and write.'),
                        ('B', 'Reading can be done using many different mediums.'),
                        ('C', 'Writing systems make unexpected demands on the brain.'),
                        ('D', 'Some brain circuits adjust to whatever is required of them.'),
                    ],
                },
                {
                    'number': 17, 'question_type': 'MCQ',
                    'content': 'According to Mark Edmundson, the attitude of college students',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', 'has changed the way he teaches.'),
                        ('B', 'has influenced what they select to read.'),
                        ('C', 'does not worry him as much as it does others.'),
                        ('D', 'does not match the views of the general public.'),
                    ],
                },
                # Q18-22: Summary completion (GAP)
                {'number': 18, 'question_type': 'GAP', 'content': 'Complete the summary. Choose ONE WORD from the box.\n\nThere have been many studies on digital screen use, showing some ___________ trends.', 'correct_answer': 'worrying'},
                {'number': 19, 'question_type': 'GAP', 'content': 'Mangen\'s team used a question-and-answer technique to find out how ___________ each group\'s understanding of the plot was.', 'correct_answer': 'thorough'},
                {'number': 20, 'question_type': 'GAP', 'content': 'The findings showed that those who read on screens found the order of information ___________ to recall.', 'correct_answer': 'hard'},
                {'number': 21, 'question_type': 'GAP', 'content': 'Studies by Ziming Liu show that students are tending to read ___________ words and phrases in a text to save time.', 'correct_answer': 'isolated'},
                {'number': 22, 'question_type': 'GAP', 'content': 'This skimming approach gives the reader only a superficial understanding of the ___________ content of material, leaving no time for deeper thought.', 'correct_answer': 'emotional'},
                # Q23-26: TFNG
                {'number': 23, 'question_type': 'TFNG', 'content': 'The medium we use to read can affect our choice of reading content.', 'correct_answer': 'TRUE'},
                {'number': 24, 'question_type': 'TFNG', 'content': 'Some age groups are more likely to lose their complex reading skills than others.', 'correct_answer': 'FALSE'},
                {'number': 25, 'question_type': 'TFNG', 'content': 'False information has become more widespread in today\'s digital era.', 'correct_answer': 'NOT GIVEN'},
                {'number': 26, 'question_type': 'TFNG', 'content': 'We still have opportunities to rectify the problems that technology is presenting.', 'correct_answer': 'TRUE'},
            ],
        },

        # ── Passage 3 ─────────────────────────────────────────────────────────
        {
            'title': 'Attitudes towards Artificial Intelligence',
            'passage_number': 3,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """Attitudes towards Artificial Intelligence

A. Artificial intelligence (AI) can already predict the future. Police forces are using it to map when and where crime is likely to occur. Doctors can use it to predict when a patient is most likely to have a heart attack or stroke. Yet, despite AI's superior forecasting abilities, people lack confidence in AI predictions and prefer trusting human experts, even when those experts are wrong. Understanding why people distrust AI is essential for its beneficial implementation.

B. Watson for Oncology exemplifies this trust problem. IBM's supercomputer promised quality recommendations for treating 12 cancers representing 80% of global cases. However, doctors faced a dilemma: when Watson agreed with their opinions, the recommendations seemed redundant; when it disagreed, doctors dismissed Watson as incompetent. The machine could not explain its complex algorithms, causing suspicion and leading physicians to ignore recommendations.

C. Trust in people develops through understanding their thinking and experiencing reliability, creating psychological safety. AI remains unfamiliar and difficult to comprehend for most people. Interacting with something we don't understand can cause anxiety and give us a sense that we're losing control. People encounter embarrassing AI failures that receive disproportionate media attention, emphasising unreliability and causing public mistrust.

D. Research showed that watching science-fiction films about AI, regardless of positive or negative portrayal, polarised participants' attitudes. Optimists became more enthusiastic; sceptics became more guarded. This 'confirmation bias' means people use evidence selectively to support existing attitudes, potentially creating a divide in society between AI beneficiaries and rejectors.

E. Previous AI experience significantly improves opinions about the technology. Transparency reports from social media companies demonstrate potential solutions. Another solution may be to reveal more about the algorithms which AI uses and the purposes they serve, helping people understand algorithmic decision-making processes.

F. Allowing user control over AI decision-making improves trust and enables AI learning. Studies show that when people modify algorithms, they feel more satisfied and more likely to use them. Providing responsibility for implementation increases acceptance of AI integration.""",
            'questions': [
                # Q27-32: Matching headings (MATCH)
                {'number': 27, 'question_type': 'MATCH', 'content': 'The reading passage has six sections A–F. Choose the correct heading for each section from the list below.\n\ni = An increasing divergence of attitudes towards AI\nii = Reasons why we have more faith in human judgement than in AI\niii = The superiority of AI projections over those made by humans\niv = The limited use of AI in one particular field\nv = The advantages of involving users in AI processes\nvi = Widespread distrust of an AI innovation\nvii = Encouraging openness about how AI functions\n\nSection A:', 'correct_answer': 'iii'},
                {'number': 28, 'question_type': 'MATCH', 'content': 'Section B:', 'correct_answer': 'vi'},
                {'number': 29, 'question_type': 'MATCH', 'content': 'Section C:', 'correct_answer': 'ii'},
                {'number': 30, 'question_type': 'MATCH', 'content': 'Section D:', 'correct_answer': 'i'},
                {'number': 31, 'question_type': 'MATCH', 'content': 'Section E:', 'correct_answer': 'vii'},
                {'number': 32, 'question_type': 'MATCH', 'content': 'Section F:', 'correct_answer': 'v'},
                # Q33-35: MCQ
                {
                    'number': 33, 'question_type': 'MCQ',
                    'content': 'What is the writer doing in Section A?',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'Outlining how AI predictions work in practice.'),
                        ('B', 'Comparing different types of AI used in medicine.'),
                        ('C', 'Highlighting the existence of a problem.'),
                        ('D', 'Suggesting ways to improve AI technology.'),
                    ],
                },
                {
                    'number': 34, 'question_type': 'MCQ',
                    'content': 'According to Section C, why might some people be reluctant to accept AI?',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', 'It leads the public to be mistrustful of AI.'),
                        ('B', 'Its complexity makes them feel that they are at a disadvantage.'),
                        ('C', 'It has replaced human decision-making in too many areas.'),
                        ('D', 'Its reliability has not been adequately demonstrated.'),
                    ],
                },
                {
                    'number': 35, 'question_type': 'MCQ',
                    'content': 'What does the writer say about the media in Section C?',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', 'It leads the public to be mistrustful of AI.'),
                        ('B', 'It exaggerates the capabilities of AI.'),
                        ('C', 'It fails to report accurately on AI developments.'),
                        ('D', 'It focuses too much on the positive aspects of AI.'),
                    ],
                },
                # Q36-40: YNNG
                {'number': 36, 'question_type': 'YNNG', 'content': 'Subjective depictions of AI in science-fiction films make people change their opinions about automation.', 'correct_answer': 'NO'},
                {'number': 37, 'question_type': 'YNNG', 'content': 'Portrayals of AI in media and entertainment are likely to become more positive.', 'correct_answer': 'NOT GIVEN'},
                {'number': 38, 'question_type': 'YNNG', 'content': 'Rejection of the possibilities of AI may have a negative effect on many people\'s lives.', 'correct_answer': 'YES'},
                {'number': 39, 'question_type': 'YNNG', 'content': 'Familiarity with AI has very little impact on people\'s attitudes to the technology.', 'correct_answer': 'NO'},
                {'number': 40, 'question_type': 'YNNG', 'content': 'AI applications which users are able to modify are more likely to gain consumer approval.', 'correct_answer': 'YES'},
            ],
        },
    ],
},


# ══════════════════════════════════════════════════════════════════════════════
# TEST 117
# ══════════════════════════════════════════════════════════════════════════════
{
    'title': 'Cambridge IELTS — Test 117',
    'difficulty': 'MEDIUM',
    'is_premium': False,
    'passages': [
        # ── Passage 1 ─────────────────────────────────────────────────────────
        {
            'title': 'The White Horse of Uffington',
            'passage_number': 1,
            'difficulty': 'MEDIUM',
            'time_limit': 20,
            'content': """The White Horse of Uffington

The cutting of huge figures or 'geoglyphs' into the earth of English hillsides has taken place for more than 3,000 years. There are 56 hill figures scattered around England, with the vast majority on the chalk downlands of the country's southern counties. The figures include giants, horses, crosses and regimental badges. Although the majority of these geoglyphs date within the last 300 years or so, there are one or two that are much older.

The most famous of these figures is perhaps also the most mysterious — the Uffington White Horse in Oxfordshire. The White Horse has recently been re-dated and shown to be even older than its previously assigned ancient pre-Roman Iron Age date. More controversial is the date of the enigmatic Long Man of Wilmington in Sussex. While many historians are convinced the figure is prehistoric, others believe that it was the work of an artistic monk from a nearby priory and was created between the 11th and 15th centuries.

The method of cutting these huge figures was simply to remove the overlying grass to reveal the gleaming white chalk below. However, the grass would soon grow over the geoglyph again unless it was regularly cleaned or scoured by a fairly large team of people. One reason that the vast majority of hill figures have disappeared is that when the traditions associated with the figures faded, people no longer bothered or remembered to clear away the grass to expose the chalk outline. Furthermore, over hundreds of years the outlines would sometimes change due to people not always cutting in exactly the same place, thus creating a different shape to the original geoglyph.

The Uffington White Horse is a unique, stylised representation of a horse consisting of a long, sleek back, thin disjointed legs, a streaming tail, and a bird-like beaked head. The horse is situated 2.5 km from Uffington village on a steep slope close to the Late Bronze Age (c. 7th century BCE) hillfort of Uffington Castle and below the Ridgeway, a long-distance Neolithic track.

The Uffington Horse is also surrounded by Bronze Age burial mounds. The carving has been placed in such a way as to make it extremely difficult to see from close quarters, and like many geoglyphs is best appreciated from the air. Nevertheless, on a clear day the carving can be seen from up to 30 km away.

The earliest evidence of a horse at Uffington is from the 1070s CE when 'White Horse Hill' is mentioned in documents from the nearby Abbey of Abingdon. However, the carving is believed to date back much further. In 1995, Optically Stimulated Luminescence (OSL) testing was carried out on soil from two of the lower layers of the horse's body. The result was a date for the horse's construction somewhere between 1400 and 600 BCE — in other words, it had a Late Bronze Age or Early Iron Age origin.

The carving may have been carried out during a Bronze or Iron Age ritual. Some researchers see the horse as representing the Celtic horse goddess Epona, who was worshipped as a protector of horses, and for her associations with fertility. However, the cult of Epona was not imported from Gaul (France) until around the first century CE — at least six centuries after the Uffington Horse was probably carved. It is possible that the carving represents a goddess in native mythology, such as Rhiannon, described in later Welsh mythology as a beautiful woman dressed in gold and riding a white horse.

The fact that geoglyphs can disappear easily, along with their associated rituals and meaning, indicates that they were never intended to be anything more than temporary gestures. But this does not lessen their importance. These giant carvings are a fascinating glimpse into the minds of their creators.""",
            'questions': [
                # Q1-8: TFNG
                {'number': 1,  'question_type': 'TFNG', 'content': 'Most geoglyphs in England are located in a particular area of the country.', 'correct_answer': 'TRUE'},
                {'number': 2,  'question_type': 'TFNG', 'content': 'There are more geoglyphs in the shape of a horse than any other creature.', 'correct_answer': 'NOT GIVEN'},
                {'number': 3,  'question_type': 'TFNG', 'content': 'A recent dating of the Uffington White Horse indicates that people were mistaken about its age.', 'correct_answer': 'TRUE'},
                {'number': 4,  'question_type': 'TFNG', 'content': 'Historians have come to an agreement about the origins of the Long Man of Wilmington.', 'correct_answer': 'FALSE'},
                {'number': 5,  'question_type': 'TFNG', 'content': 'Geoglyphs were created by people placing white chalk on the hillside.', 'correct_answer': 'FALSE'},
                {'number': 6,  'question_type': 'TFNG', 'content': 'Many geoglyphs in England are no longer visible.', 'correct_answer': 'TRUE'},
                {'number': 7,  'question_type': 'TFNG', 'content': 'The shape of some geoglyphs has been altered over time.', 'correct_answer': 'TRUE'},
                {'number': 8,  'question_type': 'TFNG', 'content': 'The fame of the Uffington White Horse is due to its size.', 'correct_answer': 'NOT GIVEN'},
                # Q9-13: GAP notes
                {'number': 9,  'question_type': 'GAP', 'content': 'Complete the notes. Write ONE WORD ONLY.\n\nLocation of the Uffington White Horse:\nNear an ancient road known as the ___________', 'correct_answer': 'Ridgeway'},
                {'number': 10, 'question_type': 'GAP', 'content': 'First reference to "White Horse Hill" appears in ___________ from the 1070s CE.', 'correct_answer': 'documents'},
                {'number': 11, 'question_type': 'GAP', 'content': 'According to OSL analysis of the surrounding ___________, the Horse has a Late Bronze Age or Early Iron Age origin.', 'correct_answer': 'soil'},
                {'number': 12, 'question_type': 'GAP', 'content': 'The Celtic goddess Epona was associated with protection of horses and ___________.', 'correct_answer': 'fertility'},
                {'number': 13, 'question_type': 'GAP', 'content': 'The carving may represent a Welsh goddess called ___________.', 'correct_answer': 'Rhiannon'},
            ],
        },

        # ── Passage 2 ─────────────────────────────────────────────────────────
        {
            'title': 'I Contain Multitudes',
            'passage_number': 2,
            'difficulty': 'HARD',
            'time_limit': 20,
            'content': """I Contain Multitudes

Microbes, most of them bacteria, have populated this planet since long before animal life developed and they will outlive us. Invisible to the naked eye, they are ubiquitous. They inhabit the soil, air, rocks and water and are present within every form of life, from seaweed and coral to dogs and humans. And, as Yong explains in his utterly absorbing and hugely important book, we mess with them at our peril.

Every species has its own colony of microbes, called a 'microbiome', and these microbes vary not only between species but also between individuals and within different parts of each individual. What is amazing is that while the number of human cells in the average person is about 30 trillion, the number of microbial ones is higher — about 39 trillion. At best, Yong informs us, we are only 50 per cent human. Indeed, some scientists even suggest we should think of each species and its microbes as a single unit, dubbed a 'holobiont'.

In each human there are microbes that live only in the stomach, the mouth or the armpit and by and large they do so peacefully. So 'bad' microbes are just microbes out of context. Microbes that sit contentedly in the human gut (where there are more microbes than there are stars in the galaxy) can become deadly if they find their way into the bloodstream. These communities are constantly changing too. Every time we eat, we swallow a million microbes in each gram of food; we are continually swapping microbes with other humans, pets and the world at large.

It's a fascinating topic and Yong, a young British science journalist, is an extraordinarily adept guide. Writing with lightness and panache, he has a knack of explaining complex science in terms that are both easy to understand and totally enthralling.

For most of human history we had no idea that microbes existed. The first man to see these extraordinarily potent creatures was a Dutch lens-maker called Antony van Leeuwenhoek in the 1670s. Using microscopes of his own design that could magnify up to 270 times, he examined a drop of water from a nearby lake and found it teeming with tiny creatures he called 'animalcules'. It wasn't until nearly two hundred years later that the research of French biologist Louis Pasteur indicated that some microbes caused disease. It was Pasteur's 'germ theory' that gave bacteria the poor image that endures today.

Yong's book is in many ways a plea for microbial tolerance, pointing out that while fewer than one hundred species of bacteria bring disease, many thousands more play a vital role in maintaining our health. In reality, says Yong, bacteria should not be viewed as either friends or foes, villains or heroes. Instead we should realise we have a symbiotic relationship, that can be mutually beneficial or mutually destructive.

What then do these millions of organisms do? The answer is pretty much everything. New research is now unravelling the ways in which bacteria aid digestion, regulate our immune systems, eliminate toxins, produce vitamins, affect our behaviour and even combat obesity. 'They actually help us become who we are,' says Yong. But we are facing a growing problem. Our obsession with hygiene, our overuse of antibiotics and our unhealthy, low-fibre diets are disrupting the bacterial balance and may be responsible for soaring rates of allergies and immune problems.

The most recent research actually turns accepted norms upside down. Studies indicate that the excessive use of household detergents and antibacterial products actually destroys the microbes that normally keep the more dangerous germs at bay. Other studies show that keeping a dog as a pet gives children early exposure to a diverse range of bacteria, which may help protect them against allergies later.

Already, in an attempt to stop mosquitoes spreading dengue fever — a disease that infects 400 million people a year — mosquitoes are being loaded with a bacterium to block the disease. In the future, our ability to manipulate microbes means we could construct buildings with useful microbes built into their walls to fight off infections.""",
            'questions': [
                # Q14-16: MCQ
                {
                    'number': 14, 'question_type': 'MCQ',
                    'content': 'What point does the writer make about microbes in the first paragraph?',
                    'correct_answer': 'D',
                    'choices': [
                        ('A', 'They adapt quickly to their environment.'),
                        ('B', 'The risk they pose has been exaggerated.'),
                        ('C', 'They are more plentiful in animal life than plant life.'),
                        ('D', 'They will continue to exist for longer than the human race.'),
                    ],
                },
                {
                    'number': 15, 'question_type': 'MCQ',
                    'content': 'In the second paragraph, the writer is impressed by the fact that',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'each species tends to have vastly different microbes.'),
                        ('B', 'some parts of the body contain relatively few microbes.'),
                        ('C', 'the average individual has more microbial cells than human ones.'),
                        ('D', 'scientists have limited understanding of how microbial cells behave.'),
                    ],
                },
                {
                    'number': 16, 'question_type': 'MCQ',
                    'content': 'What is the writer doing in the fifth paragraph?',
                    'correct_answer': 'A',
                    'choices': [
                        ('A', 'Explaining how a discovery was made.'),
                        ('B', "Comparing scientists' theories about microbes."),
                        ('C', 'Describing confusion among scientists.'),
                        ('D', 'Giving details of how microbes cause disease.'),
                    ],
                },
                # Q17-20: Summary completion (GAP)
                {'number': 17, 'question_type': 'GAP', 'content': 'Complete the summary. Write ONE WORD.\n\nIn Yong\'s view, bacteria that cause ___________ represent only a tiny fraction of all bacteria species.', 'correct_answer': 'illness'},
                {'number': 18, 'question_type': 'GAP', 'content': 'Yong believes humans have a symbiotic ___________ with bacteria that can be either beneficial or harmful.', 'correct_answer': 'partnership'},
                {'number': 19, 'question_type': 'GAP', 'content': 'Bacteria support human health through processes including aiding ___________ and producing vitamins.', 'correct_answer': 'nutrition'},
                {'number': 20, 'question_type': 'GAP', 'content': 'Our excessive focus on ___________ and overuse of antibiotics may be disrupting the bacterial balance.', 'correct_answer': 'cleanliness'},
                # Q21-26: YNNG
                {'number': 21, 'question_type': 'YNNG', 'content': 'It is possible that using antibacterial products in the home fails to have the desired effect.', 'correct_answer': 'YES'},
                {'number': 22, 'question_type': 'YNNG', 'content': 'It is a good idea to ensure that children come into contact with as few bacteria as possible.', 'correct_answer': 'NO'},
                {'number': 23, 'question_type': 'YNNG', 'content': "Yong's book contains more case studies than are necessary.", 'correct_answer': 'NOT GIVEN'},
                {'number': 24, 'question_type': 'YNNG', 'content': 'The case study about bacteria that prevent squid from being attacked may have limited appeal.', 'correct_answer': 'YES'},
                {'number': 25, 'question_type': 'YNNG', 'content': 'Efforts to control dengue fever have been surprisingly successful.', 'correct_answer': 'NOT GIVEN'},
                {'number': 26, 'question_type': 'YNNG', 'content': 'Microbes that reduce the risk of infection have already been put inside the walls of some hospital wards.', 'correct_answer': 'NO'},
            ],
        },

        # ── Passage 3 ─────────────────────────────────────────────────────────
        {
            'title': 'How to Make Wise Decisions',
            'passage_number': 3,
            'difficulty': 'MEDIUM',
            'time_limit': 20,
            'content': """How to Make Wise Decisions

Across cultures, wisdom has been considered one of the most revered human qualities. Although the truly wise may seem few and far between, empirical research examining wisdom suggests that it isn't an exceptional trait possessed by a small handful of bearded philosophers after all — in fact, the latest studies suggest that most of us have the ability to make wise decisions, given the right context.

'It appears that experiential, situational, and cultural factors are even more powerful in shaping wisdom than previously imagined,' says Associate Professor Igor Grossmann of the University of Waterloo in Ontario, Canada. 'Recent empirical findings from cognitive, developmental, social, and personality psychology cumulatively suggest that people's ability to reason wisely varies dramatically across experiential and situational contexts. Understanding the role of such contextual factors offers unique insights into understanding wisdom in daily life, as well as how it can be enhanced and taught.'

It seems that it's not so much that some people simply possess wisdom and others lack it, but that our ability to reason wisely depends on a variety of external factors. 'It is impossible to characterize thought processes attributed to wisdom without considering the role of contextual factors,' explains Grossmann. 'In other words, wisdom is not solely an "inner quality" but rather unfolds as a function of situations people happen to be in. Some situations are more likely to promote wisdom than others.'

Coming up with a definition of wisdom is challenging, but Grossmann and his colleagues have identified four key characteristics as part of a framework of wise reasoning. One is intellectual humility or recognition of the limits of our own knowledge, and another is appreciation of perspectives wider than the issue at hand. Sensitivity to the possibility of change in social relations is also key, along with compromise or integration of different attitudes and beliefs.

Grossmann and his colleagues have also found that one of the most reliable ways to support wisdom in our own day-to-day decisions is to look at scenarios from a third-party perspective, as though giving advice to a friend. Research suggests that when adopting a first-person viewpoint we focus on 'the focal features of the environment' and when we adopt a third-person, 'observer' viewpoint we reason more broadly and focus more on interpersonal and moral ideals such as justice and impartiality. Looking at problems from this more expansive viewpoint appears to foster cognitive processes related to wise decisions.

In one experiment that took place during the peak of a recent economic recession, graduating college seniors were asked to reflect on their job prospects. The students were instructed to imagine their career either 'as if you were a distant observer' or 'before your own eyes as if you were right there'. Participants in the group assigned to the 'distant observer' role displayed more wisdom-related reasoning than did participants in the control group.

In another study, couples in long-term romantic relationships were instructed to visualise an unresolved relationship conflict either through the eyes of an outsider or from their own perspective. Couples in the 'other's eyes' condition were significantly more likely to rely on wise reasoning — recognising others' perspectives and searching for a compromise — compared to the couples in the egocentric condition.

We might associate wisdom with intelligence or particular personality traits, but research shows only a small positive relationship between wise thinking and crystallised intelligence. 'It is remarkable how much people can vary in their wisdom from one situation to the next, and how much stronger such contextual effects are for understanding the relationship between wise judgment and its social and affective outcomes as compared to the generalised "traits",' Grossmann explains.""",
            'questions': [
                # Q27-30: MCQ
                {
                    'number': 27, 'question_type': 'MCQ',
                    'content': 'What point does the writer make in the first paragraph?',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', 'Wisdom appears to be unique to the human race.'),
                        ('B', 'A basic assumption about wisdom may be wrong.'),
                        ('C', 'Concepts of wisdom may depend on the society we belong to.'),
                        ('D', 'There is still much to be discovered about the nature of wisdom.'),
                    ],
                },
                {
                    'number': 28, 'question_type': 'MCQ',
                    'content': 'What does Igor Grossmann suggest about the ability to make wise decisions?',
                    'correct_answer': 'C',
                    'choices': [
                        ('A', 'It can vary greatly from one person to another.'),
                        ('B', 'Earlier research into it was based on unreliable data.'),
                        ('C', 'The importance of certain influences on it was underestimated.'),
                        ('D', 'Various branches of psychology define it according to their own criteria.'),
                    ],
                },
                {
                    'number': 29, 'question_type': 'MCQ',
                    'content': 'According to the third paragraph, Grossmann claims that the level of wisdom an individual shows',
                    'correct_answer': 'B',
                    'choices': [
                        ('A', 'can be greater than they think it is.'),
                        ('B', 'will be different in different circumstances.'),
                        ('C', 'may be determined by particular aspects of their personality.'),
                        ('D', 'should develop over time as a result of their life experiences.'),
                    ],
                },
                {
                    'number': 30, 'question_type': 'MCQ',
                    'content': 'What is described in the fifth paragraph?',
                    'correct_answer': 'D',
                    'choices': [
                        ('A', 'a difficulty encountered when attempting to reason wisely'),
                        ('B', 'an example of the type of person who is likely to reason wisely'),
                        ('C', 'a controversial view about the benefits of reasoning wisely'),
                        ('D', 'a recommended strategy that can help people to reason wisely'),
                    ],
                },
                # Q31-35: GAP summary
                {'number': 31, 'question_type': 'GAP', 'content': 'Complete the summary. Choose ONE WORD from the list.\n\nOptions: opinions / confidence / view / modesty / problems / objectivity / fairness\n\nIgor Grossmann and colleagues have established four characteristics of wise reasoning. It is important to have a certain degree of ___________ regarding the extent of our knowledge.', 'correct_answer': 'modesty'},
                {'number': 32, 'question_type': 'GAP', 'content': 'We should take into account ___________ which may not be the same as our own.', 'correct_answer': 'opinions'},
                {'number': 33, 'question_type': 'GAP', 'content': 'We should also be able to take a broad ___________ of any situation.', 'correct_answer': 'view'},
                {'number': 34, 'question_type': 'GAP', 'content': 'Grossmann also believes that it is better to regard scenarios with ___________, avoiding the first-person perspective.', 'correct_answer': 'objectivity'},
                {'number': 35, 'question_type': 'GAP', 'content': 'By adopting an objective viewpoint, we focus more on ___________ and other moral ideals, leading to wiser decision-making.', 'correct_answer': 'fairness'},
                # Q36-40: TFNG
                {'number': 36, 'question_type': 'TFNG', 'content': 'Students participating in the job prospects experiment could choose one of two perspectives to take.', 'correct_answer': 'FALSE'},
                {'number': 37, 'question_type': 'TFNG', 'content': 'Participants in the couples experiment were aware that they were taking part in a study about wise reasoning.', 'correct_answer': 'NOT GIVEN'},
                {'number': 38, 'question_type': 'TFNG', 'content': 'In the couples experiment, the length of the couples\' relationships had an impact on the results.', 'correct_answer': 'NOT GIVEN'},
                {'number': 39, 'question_type': 'TFNG', 'content': 'In both experiments, the participants who looked at the situation from a more detached viewpoint tended to make wiser decisions.', 'correct_answer': 'TRUE'},
                {'number': 40, 'question_type': 'TFNG', 'content': "Grossmann believes that a person's wisdom is determined by their intelligence to only a very limited extent.", 'correct_answer': 'TRUE'},
            ],
        },
    ],
},

]  # end TESTS


# ─────────────────────────────────────────────────────────────────────────────
# COMMAND
# ─────────────────────────────────────────────────────────────────────────────
class Command(BaseCommand):
    help = 'Seed real IELTS Academic Reading tests (Tests 117–119) with correct answers'

    def add_arguments(self, parser):
        parser.add_argument('--clear-only', action='store_true', help='Only clear existing data, do not seed')

    @transaction.atomic
    def handle(self, *args, **options):
        # 1. Clear existing reading data
        self.stdout.write('Clearing existing reading data...')
        deleted_q, _ = ReadingQuestion.objects.all().delete()
        deleted_p, _ = ReadingPassage.objects.all().delete()
        deleted_t = IELTSTest.objects.all().delete()[0]
        self.stdout.write(f'  Deleted: {deleted_p} passages, {deleted_q} questions, {deleted_t} tests')

        if options.get('clear_only'):
            self.stdout.write(self.style.SUCCESS('Done (clear only).'))
            return

        # 2. Seed new tests
        total_passages = 0
        total_questions = 0

        for test_data in TESTS:
            test = IELTSTest.objects.create(
                title=test_data['title'],
                is_premium=test_data['is_premium'],
                is_active=True,
            )
            self.stdout.write(f'\nCreated test: {test.title}')

            for p_data in test_data['passages']:
                passage = ReadingPassage.objects.create(
                    test=test,
                    title=p_data['title'],
                    passage_number=p_data['passage_number'],
                    difficulty=p_data['difficulty'],
                    time_limit=p_data['time_limit'],
                    content=p_data['content'],
                    is_standalone=False,
                    is_premium=False,
                )
                q_count = len(p_data['questions'])
                self.stdout.write(f'  Passage {p_data["passage_number"]}: {p_data["title"]} ({q_count} questions)')
                total_passages += 1

                for q_data in p_data['questions']:
                    question = ReadingQuestion.objects.create(
                        passage=passage,
                        number=q_data['number'],
                        question_type=q_data['question_type'],
                        content=q_data['content'],
                        correct_answer=q_data['correct_answer'],
                    )
                    total_questions += 1

                    # Add choices for MCQ
                    if q_data['question_type'] == 'MCQ' and 'choices' in q_data:
                        for option, text in q_data['choices']:
                            ReadingChoice.objects.create(
                                question=question,
                                option=option,
                                text=text,
                            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone! Created {len(TESTS)} tests, {total_passages} passages, {total_questions} questions.'
        ))
