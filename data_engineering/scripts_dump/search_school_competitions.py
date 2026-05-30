import subprocess, json, sys

queries = [
    '"competitions for school students" India platform database 2026',
    '"school competitions" aggregator website India list',
    'site that lists "competitions for class 9" OR "class 10" OR "class 11" India',
    '"student competitions" platform India "high school" NOT college NOT university',
    'school olympiad competition listing website India 2026',
    'best websites to find competitions for school students India',
    '"competition portal" school students India',
    'competitions for kids teenagers India database',
    'school hackathon competition India listing 2026',
    'interschool competition platform India',
    'scholasticworld.in school competitions listings',
    'olympiadtester.in competitions beyond olympiads',
    'school.noticebard.com competitions',
    'scholarshipsinindia.com competition section school',
    'dublieu.com school student competitions India',
    'competitionsforstudents.blogspot.com school',
    'schoolsindia.com contest details competitions',
    '"school competitions India" website 2025 2026 list',
    'competitions for class 6 7 8 9 10 India website',
    'science math art writing competitions school students India platform',
]

for i, q in enumerate(queries):
    print(f"=== QUERY {i+1}: {q} ===")
    sys.stdout.flush()
