"""
Generate Dashboard Design Document PDF
"""

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable
)

OUTPUT = "c:/Users/HP/OneDrive/Desktop/course_data/Dashboard_Design_Document.pdf"

# Colors
PRIMARY = HexColor("#1a73e8")
GREEN = HexColor("#34a853")
YELLOW = HexColor("#fbbc04")
RED = HexColor("#ea4335")
BLUE = HexColor("#4285f4")
GRAY = HexColor("#5f6368")
LIGHT_GRAY = HexColor("#f1f3f4")
DARK = HexColor("#202124")
WHITE = white

styles = getSampleStyleSheet()

# Custom styles
styles.add(ParagraphStyle(name='DocTitle', fontSize=24, leading=30, textColor=PRIMARY, spaceAfter=6, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='DocSubtitle', fontSize=12, leading=16, textColor=GRAY, spaceAfter=20))
styles.add(ParagraphStyle(name='SectionHeader', fontSize=18, leading=24, textColor=DARK, spaceBefore=20, spaceAfter=10, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='SubHeader', fontSize=14, leading=18, textColor=PRIMARY, spaceBefore=14, spaceAfter=8, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='SubHeader2', fontSize=12, leading=16, textColor=DARK, spaceBefore=10, spaceAfter=6, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='Body', fontSize=10, leading=14, textColor=DARK, spaceAfter=6))
styles.add(ParagraphStyle(name='BodySmall', fontSize=9, leading=12, textColor=GRAY, spaceAfter=4))
styles.add(ParagraphStyle(name='CardText', fontSize=10, leading=13, textColor=DARK, alignment=TA_CENTER))
styles.add(ParagraphStyle(name='CardNumber', fontSize=20, leading=24, textColor=PRIMARY, alignment=TA_CENTER, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='CardLabel', fontSize=8, leading=10, textColor=GRAY, alignment=TA_CENTER))
styles.add(ParagraphStyle(name='GaugeNumber', fontSize=22, leading=26, textColor=GREEN, alignment=TA_CENTER, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='GaugeLabel', fontSize=9, leading=12, textColor=GRAY, alignment=TA_CENTER))
styles.add(ParagraphStyle(name='AlertCritical', fontSize=10, leading=14, textColor=RED, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='AlertWarning', fontSize=10, leading=14, textColor=HexColor("#e37400"), fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='AlertInfo', fontSize=10, leading=14, textColor=BLUE, fontName='Helvetica-Bold'))
styles.add(ParagraphStyle(name='TOCEntry', fontSize=11, leading=16, textColor=DARK, spaceAfter=4, leftIndent=20))

def hr():
    return HRFlowable(width="100%", thickness=1, color=LIGHT_GRAY, spaceAfter=10, spaceBefore=10)

def card_table(data, col_widths=None):
    """Create a card-style table with borders."""
    if col_widths is None:
        col_widths = [120] * len(data[0])
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    return t

def progress_bar_text(label, pct, detail, filled_char="█", empty_char="░"):
    total_len = 30
    filled = int(pct / 100 * total_len)
    bar = filled_char * filled + empty_char * (total_len - filled)
    return f"  {label}\n  {bar}  {pct:.1f}%\n  {detail}"

def build_pdf():
    doc = SimpleDocTemplate(OUTPUT, pagesize=A4, topMargin=1.5*cm, bottomMargin=1.5*cm, leftMargin=2*cm, rightMargin=2*cm)
    story = []

    # ══════════════════════════════════════════════
    # TITLE PAGE
    # ══════════════════════════════════════════════
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph("Data Monitoring Dashboard", styles['DocTitle']))
    story.append(Paragraph("Course Data Verification System", styles['DocSubtitle']))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph("Design Document v1.0", styles['Body']))
    story.append(Paragraph("PhD & Masters Course Data Classification", styles['Body']))
    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("Sections: Statistics | Accuracy | Explorer | Filters | Comparison | Coverage | Alerts", styles['BodySmall']))
    story.append(Spacer(1, 2*cm))

    # Division note
    story.append(Paragraph("<b>Two Divisions:</b> PhD Data | Masters Data", styles['Body']))
    story.append(Paragraph("Each division contains identical dashboard sections with data specific to that program type.", styles['BodySmall']))

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # TABLE OF CONTENTS
    # ══════════════════════════════════════════════
    story.append(Paragraph("Table of Contents", styles['SectionHeader']))
    story.append(hr())
    toc_items = [
        "1. Statistics Section",
        "    1.1 Card Row (Key Metrics)",
        "    1.2 Official URL Crawls (Progress Bars)",
        "    1.3 University Leaderboard",
        "    1.4 Important Entity Verification Rates",
        "2. Accuracy Section",
        "    2.1 Important Section Trust Rates (3 Gauges)",
        "    2.2 Data Trustworthiness (Pie Chart)",
        "    2.3 Top Verified Data Points (Count Cards)",
        "3. Explorer Section",
        "    3.1 Level 1: University Browser",
        "    3.2 Level 2: Course List",
        "    3.3 Level 3: Course Detail View",
        "4. Filters Section",
        "    4.1 Primary Filter Bar",
        "    4.2 Section Verification Filters (Checkboxes)",
        "    4.3 Entity-Level Filters (Checkboxes)",
        "    4.4 Combined Filters",
        "    4.5 Quick Filter Presets",
        "    4.6 Results List",
        "5. Comparison Section",
        "    5.1 Course Selector",
        "    5.2 Section Tabs",
        "    5.3 Side-by-Side Comparison Views",
        "    5.4 Action Bar",
        "6. Coverage Section",
        "    6.1 Overview Cards",
        "    6.2 Section Data Coverage",
        "    6.3 Domain Coverage",
        "    6.4 University Coverage Heatmap",
        "    6.5 Gaps & Next Actions",
        "7. Alerts Section",
        "    7.1 Alert Priority Banner",
        "    7.2 Critical Alerts",
        "    7.3 Warning Alerts",
        "    7.4 Info Alerts",
    ]
    for item in toc_items:
        story.append(Paragraph(item, styles['TOCEntry']))

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 1: STATISTICS
    # ══════════════════════════════════════════════
    story.append(Paragraph("1. Statistics Section", styles['SectionHeader']))
    story.append(hr())

    # 1.1 Card Row
    story.append(Paragraph("1.1 Card Row (Key Metrics)", styles['SubHeader']))
    story.append(Paragraph("Four cards showing the most important numbers at a glance.", styles['Body']))

    card_data = [[
        Paragraph("<b>6,075</b><br/>Total Courses<br/><font size=7 color='#5f6368'>PhD programs</font>", styles['CardText']),
        Paragraph("<b>222</b><br/>Universities<br/><font size=7 color='#5f6368'>worldwide</font>", styles['CardText']),
        Paragraph("<b>9</b><br/>Domains<br/><font size=7 color='#5f6368'>broad fields</font>", styles['CardText']),
        Paragraph("<b>443</b><br/>Verified<br/><font size=7 color='#5f6368'>(14.3%)</font>", styles['CardText']),
    ]]
    story.append(card_table(card_data, [120, 120, 120, 120]))
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("<b>Total Courses</b> - all PhD .md files | <b>Universities</b> - unique colleges covered | <b>Domains</b> - CS & AI, Engineering, Natural Sciences, Business, Medicine, Social Sciences, Arts, Environment, Other | <b>Verified</b> - files with confidence >= 75", styles['BodySmall']))

    story.append(Spacer(1, 0.5*cm))

    # 1.2 Official URL Crawls
    story.append(Paragraph("1.2 Official URL Crawls (Progress Bars)", styles['SubHeader']))
    story.append(Paragraph("Shows crawl progress and section-level data coverage.", styles['Body']))

    progress_data = [
        ["Overall crawl progress:", "46.4%  (4,980 / 10,739 URLs)"],
        ["Files classified:", "50.9%  (3,091 / 6,075 courses)"],
    ]
    t = Table(progress_data, colWidths=[200, 280])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.3*cm))

    story.append(Paragraph("<b>Major Section Coverage:</b>", styles['Body']))
    section_cov = [
        ["Admission Requirements", "92.8% have crawled data"],
        ["Application Deadlines", "68.7% have crawled data"],
        ["Tuition & Fees", "65.0% have crawled data   (biggest gap)"],
    ]
    t = Table(section_cov, colWidths=[200, 280])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.5*cm))

    # 1.3 University Leaderboard
    story.append(Paragraph("1.3 University Leaderboard - Prestigious Universities Verified", styles['SubHeader']))
    story.append(Paragraph("Clean showcase of recognizable university names whose data has been verified against official sources.", styles['Body']))

    uni_data = [
        ["Harvard University", "Yale University"],
        ["Stanford University", "Princeton University"],
        ["Columbia University", "Cornell University"],
        ["Carnegie Mellon University", "Johns Hopkins University"],
        ["Imperial College London", "University of Oxford"],
        ["ETH Zurich", "University of Edinburgh"],
        ["University of Melbourne", "Rice University"],
    ]
    t = Table(uni_data, colWidths=[240, 240])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#e8eaed")),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
    ]))
    story.append(t)
    story.append(Paragraph("<i>and 174+ more universities worldwide</i>", styles['BodySmall']))

    story.append(Spacer(1, 0.5*cm))

    # 1.4 Entity Verification Rates
    story.append(Paragraph("1.4 Important Entity Verification Rates", styles['SubHeader']))
    story.append(Paragraph("Match rate = matched / (matched + mismatched). Only counted where both .md and crawled page had data.", styles['BodySmall']))

    entity_data = [
        ["Entity", "Matched", "Total", "Rate"],
        ["LOR Count", "496", "526", "94.3%"],
        ["GRE Status", "398", "432", "92.1%"],
        ["Cambridge", "325", "371", "87.6%"],
        ["IELTS", "1,106", "1,330", "83.2%"],
        ["TOEFL", "869", "1,161", "74.8%"],
        ["GPA", "304", "429", "70.9%"],
        ["App Fee", "472", "792", "59.6%"],
        ["PTE", "311", "668", "46.6%"],
        ["Duolingo", "120", "281", "42.7%"],
        ["GRE Score", "137", "409", "33.5%"],
    ]
    t = Table(entity_data, colWidths=[120, 80, 80, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 2: ACCURACY
    # ══════════════════════════════════════════════
    story.append(Paragraph("2. Accuracy Section", styles['SectionHeader']))
    story.append(hr())

    # 2.1 Trust Rates
    story.append(Paragraph("2.1 Important Section Trust Rates (3 Gauges)", styles['SubHeader']))
    story.append(Paragraph("(verified + partial) / files with data. Shows how trustworthy each section's data is.", styles['BodySmall']))

    gauge_data = [[
        Paragraph("<font size=18 color='#34a853'><b>78.5%</b></font><br/>trusted<br/><font size=7 color='#5f6368'>Tuition & Fees<br/>2,008 files checked</font>", styles['CardText']),
        Paragraph("<font size=18 color='#34a853'><b>73.6%</b></font><br/>trusted<br/><font size=7 color='#5f6368'>Deadlines<br/>2,124 files checked</font>", styles['CardText']),
        Paragraph("<font size=18 color='#34a853'><b>74.7%</b></font><br/>trusted<br/><font size=7 color='#5f6368'>Admission Req<br/>2,869 files checked</font>", styles['CardText']),
    ]]
    story.append(card_table(gauge_data, [160, 160, 160]))

    story.append(Spacer(1, 0.5*cm))

    # 2.2 Trustable Files Pie
    story.append(Paragraph("2.2 Data Trustworthiness (Pie Chart)", styles['SubHeader']))
    story.append(Paragraph("Trustable = files with confidence >= 60 (verified + safe_to_present + mostly_reliable)", styles['BodySmall']))

    pie_data = [[
        Paragraph("<font size=16 color='#34a853'><b>39.8%</b></font><br/><b>Trustable</b><br/>1,231 files<br/><font size=8>Verified + Safe + Mostly Reliable</font>", styles['CardText']),
        Paragraph("<font size=16 color='#ea4335'><b>60.2%</b></font><br/><b>Needs More Verification</b><br/>1,860 files<br/><font size=8>Use with caveat + Needs review</font>", styles['CardText']),
    ]]
    story.append(card_table(pie_data, [240, 240]))

    story.append(Spacer(1, 0.5*cm))

    # 2.3 Verified Data Points
    story.append(Paragraph("2.3 Top Verified Data Points (Count Cards)", styles['SubHeader']))

    dp_data = [[
        Paragraph("<font size=16 color='#1a73e8'><b>3,137</b></font><br/>Tuition amounts<br/><font size=7 color='#5f6368'>verified</font>", styles['CardText']),
        Paragraph("<font size=16 color='#1a73e8'><b>1,298</b></font><br/>Deadline dates<br/><font size=7 color='#5f6368'>verified</font>", styles['CardText']),
        Paragraph("<font size=16 color='#1a73e8'><b>4,551</b></font><br/>Admission entities<br/><font size=7 color='#5f6368'>verified</font>", styles['CardText']),
        Paragraph("<font size=16 color='#1a73e8'><b>8,986</b></font><br/>Total data points<br/><font size=7 color='#5f6368'>verified</font>", styles['CardText']),
    ]]
    story.append(card_table(dp_data, [120, 120, 120, 120]))

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 3: EXPLORER
    # ══════════════════════════════════════════════
    story.append(Paragraph("3. Explorer Section", styles['SectionHeader']))
    story.append(hr())
    story.append(Paragraph("Browse and drill-down view. Start broad (search universities) and drill into individual courses to see exactly what was verified.", styles['Body']))

    # 3.1 Level 1
    story.append(Paragraph("3.1 Level 1: University Browser", styles['SubHeader']))
    story.append(Paragraph("Search and browse universities. Sortable by Score, Name, or Course count.", styles['Body']))

    uni_browser = [
        ["University", "Courses", "Avg Score", "Label"],
        ["Aalto University", "36", "79.7", "safe"],
        ["Carnegie Mellon University", "10", "63.4", "mostly"],
        ["Columbia University", "16", "58.0", "caveat"],
        ["ETH Zurich", "102", "53.8", "caveat"],
        ["Harvard University", "19", "64.3", "mostly"],
        ["Imperial College London", "88", "64.9", "mostly"],
        ["...", "", "", ""],
    ]
    t = Table(uni_browser, colWidths=[200, 60, 70, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)
    story.append(Paragraph("<i>Click a university to see its courses (Level 2)</i>", styles['BodySmall']))

    story.append(Spacer(1, 0.5*cm))

    # 3.2 Level 2
    story.append(Paragraph("3.2 Level 2: Course List (Per University)", styles['SubHeader']))

    course_list = [
        ["Course", "Score", "T", "D", "A"],
        ["PhD Artificial Intelligence", "81.6", "V", "V", "~"],
        ["PhD Spatial Planning", "99.9", "V", "V", "V"],
        ["PhD Energy Engineering", "82.3", "-", "V", "V"],
        ["PhD Applied Math", "58.0", "-", "V", "~"],
        ["PhD Computer Science", "45.0", "X", "~", "~"],
    ]
    t = Table(course_list, colWidths=[220, 50, 30, 30, 30])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)
    story.append(Paragraph("V = verified  |  ~ = partial  |  X = flagged  |  - = no data  |  T = Tuition  D = Deadlines  A = Admission", styles['BodySmall']))

    story.append(Spacer(1, 0.5*cm))

    # 3.3 Level 3
    story.append(Paragraph("3.3 Level 3: Course Detail View", styles['SubHeader']))
    story.append(Paragraph("Full detail view for a single course. Shows summary, per-section status with source URLs, and entity-by-entity comparison table for admission requirements.", styles['Body']))

    story.append(Paragraph("<b>Course:</b> Aalto University - PhD Artificial Intelligence", styles['Body']))
    story.append(Paragraph("<b>Score:</b> 81.6  |  <b>Label:</b> safe_to_present_as_official", styles['Body']))
    story.append(Paragraph("<b>Summary:</b> Tuition: VERIFIED. Deadlines: VERIFIED (rolling). Admission: PARTIAL (6 matched, 1 mismatched).", styles['Body']))

    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph("<b>Admission Entity Comparison Table:</b>", styles['SubHeader2']))

    entity_compare = [
        ["Entity", "Status", ".md Value", "Official Value"],
        ["TOEFL", "V matched", "92", "92"],
        ["IELTS", "V matched", "6.5", "6.5"],
        ["PTE", "V matched", "62", "62"],
        ["App Fee", "V matched", "no fee", "no fee"],
        ["GRE Status", "V matched", "not required", "not required"],
        ["GPA", "V matched", "3.5", "3.5"],
        ["GRE Score", "X mismatch", "202", "180"],
        ["Duolingo", "- n/a", "", ""],
        ["Cambridge", "- n/a", "", ""],
        ["LOR Count", "- n/a", "", ""],
    ]
    t = Table(entity_compare, colWidths=[90, 80, 100, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, 6), HexColor("#e6f4ea")),  # green for matched
        ('BACKGROUND', (0, 7), (-1, 7), HexColor("#fce8e6")),  # red for mismatch
        ('BACKGROUND', (0, 8), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Paragraph("Actions: [View .md file] [View crawled page] [Open Official URL]", styles['BodySmall']))

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 4: FILTERS
    # ══════════════════════════════════════════════
    story.append(Paragraph("4. Filters Section", styles['SectionHeader']))
    story.append(hr())

    # 4.1 Primary Filter Bar
    story.append(Paragraph("4.1 Primary Filter Bar", styles['SubHeader']))
    story.append(Paragraph("Always visible at top. Filter by confidence score, university, domain, and course name.", styles['Body']))

    filter_bar = [
        ["Confidence:", "[All] [>=75] [>=60] [>=50] [<40]"],
        ["University:", "[Search/Select dropdown]"],
        ["Domain:", "[All] [CS & AI] [Engineering] [Sciences] [...]"],
        ["Program:", "[Search course name...]"],
    ]
    t = Table(filter_bar, colWidths=[100, 380])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.5*cm))

    # 4.2 Section Verification Filters
    story.append(Paragraph("4.2 Section Verification Filters (Checkboxes)", styles['SubHeader']))
    story.append(Paragraph("Tick multiple boxes. AND logic across sections, OR within a section.", styles['Body']))

    sv_data = [
        ["Section", "Verified", "Partial", "Flagged", "No Data"],
        ["Tuition & Fees", "[ ] 603", "[ ] 973", "[ ] 432", "[ ] 1,083"],
        ["Application Deadlines", "[ ] 812", "[ ] 751", "[ ] 561", "[ ] 967"],
        ["Admission Requirements", "[ ] 864", "[ ] 1,279", "[ ] 726", "[ ] 222"],
    ]
    t = Table(sv_data, colWidths=[140, 80, 80, 80, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.5*cm))

    # 4.3 Entity-Level Filters
    story.append(Paragraph("4.3 Entity-Level Filters (Checkboxes)", styles['SubHeader']))

    ef_data = [
        ["Entity", "Matched", "Mismatched", "Not on page", "N/A"],
        ["TOEFL", "[ ] 869", "[ ] 292", "[ ] 1,003", "[ ] 297"],
        ["IELTS", "[ ] 1,106", "[ ] 224", "[ ] 994", "[ ] 189"],
        ["GRE Status", "[ ] 398", "[ ] 34", "[ ] 78", "[ ] 0"],
        ["GPA", "[ ] 304", "[ ] 125", "[ ] 2,002", "[ ] 11"],
        ["App Fee", "[ ] 472", "[ ] 320", "[ ] 490", "[ ] 1,457"],
        ["LOR Count", "[ ] 496", "[ ] 30", "[ ] 412", "[ ] 1,801"],
        ["PTE", "[ ] 311", "[ ] 357", "[ ] 446", "[ ] 559"],
        ["Duolingo", "[ ] 120", "[ ] 161", "[ ] 264", "[ ] 1,108"],
        ["GRE Score", "[ ] 137", "[ ] 272", "[ ] 78", "[ ] 0"],
    ]
    t = Table(ef_data, colWidths=[90, 80, 80, 90, 80])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.5*cm))

    # 4.4 Combined Filters
    story.append(Paragraph("4.4 Combined Filters (Power User)", styles['SubHeader']))
    story.append(Paragraph("AND logic across sections + entities. Example: 'Show courses where Tuition=Verified AND TOEFL=Mismatched'", styles['Body']))

    # 4.5 Quick Presets
    story.append(Paragraph("4.5 Quick Filter Presets (One-click)", styles['SubHeader']))
    presets = [
        ["[All Verified Courses]", "All 3 sections verified"],
        ["[Needs Attention]", "Score < 40"],
        ["[Stale Deadlines]", "Deadlines flagged as past"],
        ["[Missing Tuition Data]", "Tuition = no_data"],
        ["[TOEFL/IELTS Mismatches]", "Either test score doesn't match"],
        ["[Fully Crawled But Low]", "All 3 sections have data, score < 50"],
        ["[High Score Missing Data]", "Score >= 60 but has no_data sections"],
    ]
    t = Table(presets, colWidths=[200, 280])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 10),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 4.6 Results List
    story.append(Paragraph("4.6 Results List (Dynamic, below filters)", styles['SubHeader']))
    story.append(Paragraph("Updates dynamically as filters are applied. Includes [Export CSV] and pagination.", styles['Body']))

    results_table = [
        ["University", "Course", "Score", "T", "D", "A"],
        ["Aalto University", "PhD Spatial Planning", "99.9", "V", "V", "V"],
        ["Aalto University", "PhD AI", "81.6", "V", "V", "~"],
        ["Case Western Reserve", "PhD Clinical Research", "83.3", "V", "V", "~"],
        ["Cornell University", "PhD Applied Physics", "75.0", "V", "V", "~"],
    ]
    t = Table(results_table, colWidths=[140, 160, 45, 25, 25, 25])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('ALIGN', (2, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 5: COMPARISON
    # ══════════════════════════════════════════════
    story.append(Paragraph("5. Comparison Section", styles['SectionHeader']))
    story.append(hr())
    story.append(Paragraph("Side-by-side view of .md file data vs official crawled page data. For manual verification of any course.", styles['Body']))

    # 5.1 Course Selector
    story.append(Paragraph("5.1 Course Selector", styles['SubHeader']))
    story.append(Paragraph("Pick university and course. Shows score, label, and one-line summary.", styles['Body']))

    # 5.2 Section Tabs
    story.append(Paragraph("5.2 Section Tabs", styles['SubHeader']))
    story.append(Paragraph("[ Tuition & Fees (V) ]  [ Application Deadlines (V) ]  [ Admission Requirements (~) ]", styles['Body']))
    story.append(Paragraph("Click any tab to load comparison for that section.", styles['BodySmall']))

    # 5.3 Side-by-Side
    story.append(Paragraph("5.3 Side-by-Side Comparison View", styles['SubHeader']))

    compare_data = [
        [".md File Says", "Official Page Says"],
        ["TOEFL iBT: minimum score of 92.", "TOEFL iBT: 92"],
        ["IELTS: minimum 6.5 overall band score.", "IELTS: 6.5"],
        ["GRE: Not required.", "No GRE required."],
        ["GPA: 3.5", "GPA of 3.5 or above required."],
        ["GRE Score: minimum 202  [MISMATCH]", "...minimum 180...  [DIFFERENT]"],
    ]
    t = Table(compare_data, colWidths=[240, 240])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, 4), HexColor("#e6f4ea")),
        ('BACKGROUND', (0, 5), (-1, 5), HexColor("#fce8e6")),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t)
    story.append(Paragraph("Green = matched  |  Red = mismatched", styles['BodySmall']))

    story.append(Spacer(1, 0.3*cm))

    # 5.4 Action Bar
    story.append(Paragraph("5.4 Action Bar", styles['SubHeader']))
    story.append(Paragraph("[Previous Course]  [Next Course]  [Open .md File]  [Open Official URL]  [Mark as Reviewed]", styles['Body']))

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 6: COVERAGE
    # ══════════════════════════════════════════════
    story.append(Paragraph("6. Coverage Section", styles['SectionHeader']))
    story.append(hr())

    # 6.1 Overview Cards
    story.append(Paragraph("6.1 Overview Cards", styles['SubHeader']))
    cov_cards = [[
        Paragraph("<font size=14 color='#1a73e8'><b>3,091</b></font><br/>Classified<br/><font size=7>of 6,075 (50.9%)</font>", styles['CardText']),
        Paragraph("<font size=14 color='#ea4335'><b>2,984</b></font><br/>Not Yet<br/><font size=7>Classified (49.1%)</font>", styles['CardText']),
        Paragraph("<font size=14 color='#1a73e8'><b>188</b></font><br/>Universities<br/><font size=7>of 222 (84.7%)</font>", styles['CardText']),
        Paragraph("<font size=14 color='#34a853'><b>20</b></font><br/>Fully Covered<br/><font size=7>Universities (10.6%)</font>", styles['CardText']),
    ]]
    story.append(card_table(cov_cards, [120, 120, 120, 120]))

    story.append(Spacer(1, 0.3*cm))

    # 6.2 Section Coverage
    story.append(Paragraph("6.2 Section Data Coverage", styles['SubHeader']))
    sc_data = [
        ["Section", "Covered", "Missing", "%"],
        ["Admission Requirements", "2,869", "222", "92.8%"],
        ["Application Deadlines", "2,124", "967", "68.7%"],
        ["Tuition & Fees", "2,008", "1,083", "65.0%"],
    ]
    t = Table(sc_data, colWidths=[180, 80, 80, 60])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 6.3 Domain Coverage
    story.append(Paragraph("6.3 Coverage by Academic Domain", styles['SubHeader']))
    domain_data = [
        ["Domain", "Courses", "Reliable", "%"],
        ["Natural Sciences", "676", "263", "39%"],
        ["Engineering", "332", "127", "38%"],
        ["Social Sciences & Humanities", "307", "125", "41%"],
        ["Computer Science & AI", "219", "80", "37%"],
        ["Medicine & Health", "190", "80", "42%"],
        ["Business & Economics", "172", "72", "42%"],
    ]
    t = Table(domain_data, colWidths=[180, 70, 70, 50])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), PRIMARY),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#dadce0")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#dadce0")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 6.4 Heatmap description
    story.append(Paragraph("6.4 University Coverage Heatmap", styles['SubHeader']))
    story.append(Paragraph("Per-university x per-section coverage grid. Color coded: >75% covered (green), 25-75% (yellow), <25% (red). Sortable by Name, Courses, Worst Coverage.", styles['Body']))

    # 6.5 Gaps
    story.append(Paragraph("6.5 Gaps & Next Actions", styles['SubHeader']))
    gaps = [
        ["1.", "2,984 courses have zero crawled data. Resume crawling for remaining 49%."],
        ["2.", "Tuition & Fees has lowest coverage (65%). 1,083 courses missing."],
        ["3.", "50 universities have no fully-verified courses. Priority crawl needed."],
        ["4.", "Computer Science & AI has lowest reliable rate (37%). High-demand domain."],
    ]
    t = Table(gaps, colWidths=[25, 455])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor("#fef7e0")),
        ('BOX', (0, 0), (-1, -1), 1, HexColor("#f9ab00")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
    ]))
    story.append(t)

    story.append(PageBreak())

    # ══════════════════════════════════════════════
    # SECTION 7: ALERTS
    # ══════════════════════════════════════════════
    story.append(Paragraph("7. Alerts Section", styles['SectionHeader']))
    story.append(hr())

    # 7.1 Banner
    story.append(Paragraph("7.1 Alert Priority Banner", styles['SubHeader']))
    banner = [["CRITICAL: 3 alerts  |  WARNING: 3 alerts  |  INFO: 3 alerts"]]
    t = Table(banner, colWidths=[480])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HexColor("#fce8e6")),
        ('BOX', (0, 0), (-1, -1), 1, RED),
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 7.2 Critical
    story.append(Paragraph("7.2 Critical Alerts", styles['SubHeader']))
    critical_alerts = [
        ["Alert", "Count", "Impact"],
        ["Tuition Amounts Wrong", "432", "Students see incorrect fees"],
        ["TOEFL/IELTS Mismatches", "516", "Wrong score targets for applicants"],
        ["Prestigious Uni Data Issues", "34", "High-visibility programs unreliable"],
    ]
    t = Table(critical_alerts, colWidths=[180, 60, 240])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), RED),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor("#fce8e6")),
        ('BOX', (0, 0), (-1, -1), 1, RED),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#f5c6cb")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 7.3 Warning
    story.append(Paragraph("7.3 Warning Alerts", styles['SubHeader']))
    warning_alerts = [
        ["Alert", "Count", "Action"],
        ["Reliable Files With Flagged Section", "175", "Review flagged section only"],
        ["Multiple Admission Mismatches", "140", "Likely wrong source page"],
        ["Stale Deadlines", "45", "Website updated, refresh needed"],
    ]
    t = Table(warning_alerts, colWidths=[200, 50, 230])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), YELLOW),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor("#fef7e0")),
        ('BOX', (0, 0), (-1, -1), 1, YELLOW),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#fde68a")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.3*cm))

    # 7.4 Info
    story.append(Paragraph("7.4 Info Alerts", styles['SubHeader']))
    info_alerts = [
        ["Alert", "Count", "Note"],
        ["Past Deadlines", "128", "Data correct at time of writing, needs refresh"],
        ["Uncrawled Courses", "2,984", "Resume crawling for remaining 49%"],
        ["Zero Confidence Files", "10", "May have wrong official URLs"],
    ]
    t = Table(info_alerts, colWidths=[150, 50, 280])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), WHITE),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor("#e8f0fe")),
        ('BOX', (0, 0), (-1, -1), 1, BLUE),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, HexColor("#aecbfa")),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('ALIGN', (1, 0), (1, -1), 'CENTER'),
    ]))
    story.append(t)

    story.append(Spacer(1, 0.5*cm))
    story.append(Paragraph("Each alert is clickable and shows the full list of affected courses with sort and [Export CSV] options.", styles['BodySmall']))

    # Build
    doc.build(story)
    print(f"PDF saved to: {OUTPUT}")


if __name__ == "__main__":
    build_pdf()
