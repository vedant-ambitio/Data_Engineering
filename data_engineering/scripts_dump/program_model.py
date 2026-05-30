
from django.db import models
from django.contrib.postgres.fields import ArrayField
from common.models import City,Country,State
from storages.backends.s3boto3 import S3Boto3Storage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.contrib.postgres.search import SearchVectorField
from django.contrib.postgres.indexes import GinIndex



class StudentDiversity(models.Model):
    university = models.ForeignKey('University',on_delete=models.CASCADE,null=True,blank=True)
    label = models.CharField(max_length=255)
    value = models.IntegerField()

    
class University(models.Model):

    UNIVERSITY_TYPE = (
        ('Unknown','Unknown'),
        ('Public','Public'),
        ('Private','Private'),
    )

    name = models.CharField(max_length=255,unique=True)
    is_lab = models.BooleanField(default=False)
    qs_name = models.CharField(max_length=255,null=True,blank=True)
    slug = models.SlugField(blank=True, null=True, max_length=100)

    ope_id = models.IntegerField(null=True,blank=True)

    type = models.CharField(choices=UNIVERSITY_TYPE, max_length=10, default='Unknown')

    pointers = ArrayField(models.CharField(max_length=500), blank=True, null=True)
    reasons_to_consider = ArrayField(models.CharField(max_length=500), blank=True, null=True)

    shortName = models.CharField(max_length=255, blank=True, null=True)
    logo = models.ImageField(upload_to='programs/university/logo/', default='programs/university/default_logo.jpg',max_length=255,storage=S3Boto3Storage())
    shortLogo = models.ImageField(upload_to='programs/university/short-logo/', default='programs/university/default_logo.jpg',max_length=255,storage=S3Boto3Storage())
    galleryImages = ArrayField(models.URLField(), default=list,blank=True)
    globalRank = models.IntegerField(blank=True, null=True)
    qsRank = models.IntegerField(blank=True, null=True)
    qsRankYear = models.IntegerField(blank=True, null=True)
    thRank = models.IntegerField(blank=True, null=True)
    thRankYear = models.IntegerField(blank=True, null=True)
    usnRank = models.IntegerField(blank=True, null=True)
    usnRankYear = models.IntegerField(blank=True, null=True)
    universityPageLink  = models.CharField(null=True,blank=True)
    cityName = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)
    countryName = models.ForeignKey(Country, on_delete=models.SET_NULL, null=True, blank=True)
    stateName = models.ForeignKey(State, on_delete=models.SET_NULL, null=True, blank=True)
    address = models.CharField(max_length=1024, blank=True, null=True)
    isActive = models.BooleanField(default=True)
    menPercentage = models.FloatField(null=True,blank=True)
    womenPercentage = models.FloatField(null=True,blank=True)
    acceptanceRate = models.DecimalField(max_digits=5, decimal_places=2,validators=[MinValueValidator(1), MaxValueValidator(100)],blank=True, null=True)
    search_vector = SearchVectorField(null=True)
    tags = ArrayField(models.CharField(max_length=255), default=list)
    studentInternationalDiversity = models.FloatField(blank=True, null=True)  
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, auto_now_add=False)

    class Meta:
        indexes = [GinIndex(fields=['search_vector'])]

    def __str__(self):
        return f"{self.name}"

    def getRankObject(self):
        rankData = []
        if self.usnRank:
            rankData.append({"name": "US World and News Report", "rank": self.usnRank, "year": self.usnRankYear,
                            "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/usn-rank.svg"})
        if self.thRank:
            rankData.append({"name": "The World University Rankings", "rank": self.thRank, "year": self.thRankYear,
                            "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/th-rank.svg"})
        if self.qsRank:
            rankData.append({"name": "QS World University Rankings", "year": self.qsRankYear,
                            "rank": self.qsRank, "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/qs-rank.svg"})
        return rankData
    
    def getUniversityDetailRankData(self):
        rankData = []
        if self.usnRank:
            rankData.append({"name": "US World and News Report", "rank": str(self.usnRank), 
                            "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/usn-rank.svg"})
        if self.thRank:
            rankData.append({"name": "The World University Rankings", "rank": str(self.thRank), 
                            "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/th-rank.svg"})
        if self.qsRank:
            rankData.append({"name": "QS World University Rankings", 
                            "rank": str(self.qsRank), "image": "https://ambitio-django-backend-media.s3.ap-south-1.amazonaws.com/programs/static/qs-rank.svg"})
        return rankData

    
    def getStudentDiversityData(self):
        colorList = ['#FF6384','#36A2EB','#FFCE56', '#9966FF','#FF9F40','#FFCC99','#00E396','#FF6384','#1C8CDC','#FFD700','#FF6384','#808080','#C71585','#FFC0CB']
        otherColor = "#4BC0C0"
        studentDiversity = []
        for n, sd in enumerate(self.studentdiversity_set.all()):
            color = colorList[n]
            if sd.label.lower().find('other')>=0:
                color = otherColor
            studentDiversity.append({'id': n,"label": sd.label, "value": sd.value, "color": color})
        return studentDiversity
    
    class Meta:
        indexes = [
            models.Index(fields=['qsRank']),
        ]    





TERM_CHOICES = (
    ('spring', 'spring'),
    ('fall', 'fall'),
    ('winter', 'winter'),
    ('summer', 'summer'),
)


class CourseMajor(models.Model):
    name = models.CharField(max_length=255)
    isActive = models.BooleanField(default=True)
    isSTEM = models.BooleanField(default=False)
    search_vector = SearchVectorField(null=True)
    category = models.CharField(max_length=255,null=True,blank=True)
    insight = models.TextField(null=True,blank=True)
    tags = ArrayField(models.CharField(max_length=255), blank=True, null=True) 

    class Meta:
        indexes = [GinIndex(fields=['search_vector'])]

    def __str__(self):
        return f"{self.name}"


class CourseSpecialization(models.Model):

    name = models.CharField(max_length=255)
    isActive = models.BooleanField(default=True)
    logo = models.ImageField(upload_to='programs/course-specialization',storage=S3Boto3Storage(),null=True,blank=True)
    domain_cagr = models.FloatField(null=True,blank=True, help_text="Domain CAGR (Compound Annual Growth Rate) from gemini data")
    search_vector = SearchVectorField(null=True)

    class Meta:
        indexes = [GinIndex(fields=['search_vector'])]

    def __str__(self):
        return f"{self.name}"


class CourseDegree(models.Model):
    name = models.CharField(max_length=255)
    isActive = models.BooleanField(default=True)
    search_vector = SearchVectorField(null=True)

    class Meta:
        indexes = [GinIndex(fields=['search_vector'])]

    def __str__(self):
        return f"{self.name}"


class Company(models.Model):
    name = models.CharField(max_length=255)
    logo = models.ImageField(upload_to='programs/company/logo/', default='programs/company/default_logo.jpeg', max_length=255,storage=S3Boto3Storage())
    isActive = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name}"


class JobRole(models.Model):
    name = models.CharField(max_length=255)
    isActive = models.BooleanField(default=True)
    associatedMajors = models.ManyToManyField(CourseMajor,blank=True)
    search_vector = SearchVectorField(null=True)

    class Meta:
        indexes = [GinIndex(fields=['search_vector'])]

    def __str__(self):
        return f"{self.name}"


class ApplicationDeadline(models.Model):
    program = models.ForeignKey('Program', on_delete=models.CASCADE, null=True, blank=True, related_name='application_deadlines')
    intake = models.CharField(choices=TERM_CHOICES, max_length=10)
    round = models.CharField(max_length=100)
    label = models.CharField(max_length=255,null=True,blank=True)
    tags = ArrayField(models.CharField(max_length=100), default=list)
    deadline = models.DateField(null=True,blank=True)
    decision = models.DateField(null=True,blank=True)
    isActive = models.BooleanField(default=True)
    decisionDescription = models.TextField(null=True,blank=True)


class ProgramExamTest(models.Model):
    name = models.CharField(max_length=25)
    type = models.CharField(max_length=25,default="Unknown")
    logo = models.ImageField(upload_to='programs/examtest/logo/', blank=True, null=True,storage=S3Boto3Storage())
    totalScore = models.FloatField(blank=True, null=True)

    def __str__(self):
        return f"{self.name}"



class TestScoreField(models.Model):
    score = models.ForeignKey('AdmissionMinimumExamScores',on_delete=models.CASCADE,null=True,blank=True, related_name='test_score_fields')
    fieldName = models.CharField(max_length=255)
    value = models.CharField(max_length=255,null=True,blank=True)

class AdmissionMinimumExamScores(models.Model):

    program = models.ForeignKey('Program', on_delete=models.CASCADE, null=True, blank=True, related_name='admission_minimum_exam_scores')
    examTest = models.ForeignKey(ProgramExamTest, on_delete=models.SET_NULL,null=True)
    isRequired = models.CharField(max_length=20, default='Unknown')
    minScore =  models.FloatField(blank=True, null=True)
    averageScore = models.FloatField(blank=True, null=True)
    minScoreDescription = models.CharField(null=True,blank=True)


class ScholarshipProvider(models.Model):
    name = models.CharField(max_length=255)


class ProgramFAQ(models.Model):
    program = models.ForeignKey('Program', on_delete=models.CASCADE, null=True, blank=True, related_name='program_faqs')
    question = models.CharField(max_length=255)
    answer = models.TextField()

class EntryRequirements(models.Model):
    
    name = models.CharField(max_length=255,null=True,blank=True)
    value = models.CharField(max_length=255,null=True,blank=True)
    parentEntryRequirement = models.CharField(max_length=255,null=True,blank=True)
    isPrimaryRequirement = models.BooleanField(default=True)
    isActive = models.BooleanField(default=True)
    isEditable = models.BooleanField(default=False)


class ProgramEntryRequirements(models.Model):

    program = models.ForeignKey('Program', on_delete=models.CASCADE, null=True, blank=True, related_name='program_entry_requirements')
    entryRequirement = models.ForeignKey(EntryRequirements,on_delete=models.CASCADE)
    count = models.IntegerField(default=1)
    requirementDetail = models.CharField(max_length=255,null=True,blank=True)
    description = models.CharField(null=True,blank=True)


class EligbilityCriteria(models.Model):
    
    name = models.CharField(max_length=255,null=True,blank=True)
    value = models.CharField(max_length=255,null=True,blank=True)
    fields = ArrayField(models.CharField(null=True,blank=True), default=list)
    isActive = models.BooleanField(default=True)

class ProgramEligbilityCriteria(models.Model):

    program = models.ForeignKey('Program', on_delete=models.CASCADE, null=True, blank=True, related_name='program_eligibility_criteria')
    eligibilityCriteria = models.ForeignKey(EligbilityCriteria,on_delete=models.CASCADE)
    criteria = models.JSONField(null=True,blank=True)
    details = models.TextField(null=True,blank=True)


class ProgramApplicationProcess(models.Model):
    program = models.ForeignKey(
        'Program', on_delete=models.CASCADE, null=True, blank=True,
        related_name='program_application_process'
    )
    step = models.CharField(max_length=255, null=True, blank=True)
    description = models.TextField(null=True, blank=True)


class Program(models.Model):
    GRE_REQUIRED_CHOICES = (
        ('Unknown','Unknown'),
        ('Yes', 'Yes'),
        ('No', 'No'),
        ('Waived', 'Waived'),
    )
    OPTIONAL_CHOICES = (
        ('Yes', 'Yes'),
        ('No', 'No'),
        ('Optional', 'Optional')
    )
    COURSES_CHOICES = (
        ('Bachelor','Bachelor'),
        ('Master','Master'),
        ('PhD','PhD'),
        ('Unknown','Unknown')
    )
    PROGRAM_TYPE_CHOICES = (
        ('PROGRAM', 'Program'),
        ('JOB_POSITION', 'Job Position'),
    )

    type = models.CharField(
        choices=PROGRAM_TYPE_CHOICES,
        max_length=20,
        default='PROGRAM',
        db_index=True,
        help_text='PROGRAM for courses; JOB_POSITION for merged job positions.',
    )
    university = models.ForeignKey(University, on_delete=models.SET_NULL,null=True)
    courseSpecialization = models.ForeignKey(CourseSpecialization, on_delete=models.SET_NULL,null=True)
    courseDegree = models.ForeignKey(CourseDegree,on_delete=models.SET_NULL,null=True)
    courseMajor = models.ForeignKey(CourseMajor, on_delete=models.SET_NULL,null=True,blank=True)
    courseLevel = models.CharField(choices=COURSES_CHOICES, max_length=25, default='Unknown')
    programRank = models.IntegerField(blank=True, null=True)
    is_job_role = models.BooleanField(default=False)

    pointers = ArrayField(models.CharField(max_length=500), blank=True, null=True)
    why_study_points = ArrayField(models.CharField(max_length=500), default=list, blank=True)
    reasons_to_consider = ArrayField(models.CharField(max_length=500), default=list, blank=True)

    program_insight = models.TextField(null=True,blank=True)
    application_requirements_insight = models.TextField(null=True,blank=True)
    test_score_insight = models.TextField(null=True,blank=True)
    testScoreDescription = models.TextField(null=True, blank=True)

    schoolName = models.CharField(max_length=255, blank=True, null=True)
    totalTuitionFeePerYear = models.FloatField(blank=True, null=True)
    tuitionFeePerYear = models.FloatField(blank=True, null=True)
    officialPageLink = models.URLField(blank=True, null=True, max_length=500)
    admission_requirement_url = models.URLField(blank=True, null=True, max_length=500)
    eligibility_criteria_url = models.URLField(blank=True, null=True, max_length=500)
    application_process_url = models.URLField(blank=True, null=True, max_length=500)
    application_checklist_page = models.URLField(blank=True, null=True, max_length=500)
    selected_urls = models.JSONField(null=True,)
    courseDuration = models.IntegerField(verbose_name='Course Duration(months)',blank=True, null=True)

    roi_score = models.FloatField(default=1,null=True,blank=True)
    
    totalTuitionFee = models.FloatField(blank=True, null=True)
    tuitionFee = models.FloatField(blank=True, null=True)
    applicationFee = models.CharField(max_length=255,blank=True, null=True)
    applicationFeeCurrency = models.CharField(null=True,blank=True)
    applicationFeeDescription = models.CharField(null=True,blank=True)

    intake = ArrayField(models.CharField(choices=TERM_CHOICES, max_length=10), default=list)
    isGRERequired = models.CharField(choices=GRE_REQUIRED_CHOICES, max_length=10, default='Unknown')
    overviewDescription = models.TextField(default="")

    additional_requirements = ArrayField(models.TextField(null=True,blank=True), default=list)
    additional_criteria = ArrayField(models.TextField(null=True,blank=True), default=list)

    classSize = models.IntegerField(blank=True, null=True)
    averageAge = models.IntegerField(blank=True, null=True)
    undergradEnrollment = models.IntegerField(blank=True, null=True)
    averageWorkExperience = models.IntegerField(blank=True, null=True)
    acceptanceRate = models.DecimalField(max_digits=5, decimal_places=2,validators=[MinValueValidator(1), MaxValueValidator(100)],blank=True, null=True)
    interviewRequested = models.CharField(max_length=10, choices=OPTIONAL_CHOICES, default='No')
    entryRequirementsDetails = models.TextField(default="")
    entryRequirementsTags = ArrayField(models.CharField(max_length=255), default=list)

    greSchoolCode = models.CharField(max_length=50, blank=True, null=True)
    gmatSchoolCode = models.CharField(max_length=50, blank=True, null=True)
    toeflSchoolCode = models.CharField(max_length=50, blank=True, null=True)

    officialLinks = ArrayField(models.URLField(max_length=500), default=list)


    scholarships = models.ManyToManyField('scholarships.Scholarship',blank=True)
    view_priority = models.IntegerField(default=10)
    careerOutComeDescription = models.TextField(null=True,blank=True)
    averageBaseSalary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    medianBaseSalary = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    prospectiveJobRoles = models.ManyToManyField('JobRole', related_name='programProspectiveJobRoles', blank=True)
    recruiters = models.ManyToManyField('Company', related_name='programRecruiters', blank=True)    
    overallCostPerYear = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    avgCostOfLivingPerYear = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    totalCostOfLivingPerYear = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    fundingOptionsTags = ArrayField(models.CharField(max_length=255), default=list)
    fundingOptionsDetails = models.TextField(default="")
    funding_benefits = ArrayField(models.TextField(), default=list, blank=True)
    expected_research_output_responsibilities = ArrayField(models.TextField(), default=list, blank=True)
    scholarshipsDetails = models.TextField(default="")
    scholarshipsProviders = models.ManyToManyField('ScholarshipProvider', blank=True)
    whoIsThisProgramForDescription = models.TextField(null=True,blank=True)
    applicationDeadlineDescription = models.TextField(null=True,blank=True)
    eligibilityCriteriaDescription = models.TextField(null=True,blank=True)
    retentionRate = models.FloatField(null=True,blank=True)
    classProfileDescription = models.TextField(null=True,blank=True)
    studentsDescription = models.TextField(null=True,blank=True)
    fulltimeEnrollments = models.IntegerField(blank=True, null=True)
    currencySymbol = models.CharField(blank=True,null=True)
    feesAndFundingDescription = models.TextField(null=True,blank=True)
    admissionPolicy = models.CharField(max_length=255,null=True,blank=True)
    internationalStudentsPercentage = models.FloatField(null=True,blank=True)
    averageTotalAidAwarded = models.CharField(max_length=255,null=True,blank=True)
    graduationRate = models.FloatField(null=True,blank=True)
    jobPlacementPercentage = models.FloatField(null=True,blank=True)
    funding_availability = models.CharField(max_length=50, null=True, blank=True, help_text="Funding availability level (Low/Medium/High) from gemini data")
    cost_of_living_index = models.FloatField(null=True, blank=True, help_text="Cost of Living Index for the university's city (New York = 100 baseline) from gemini data")
    isVerified = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)

    admission_requirements_data = models.JSONField(default=dict)
    eligibility_criteria_data = models.JSONField(default=dict)
    faculty_data = models.JSONField(default=list)

    work_experience_in_months = models.IntegerField(null=True,blank=True)
    work_experience_description = models.TextField(null=True,blank=True)
    
    supervisors = models.ManyToManyField('supervisors.Supervisor', blank=True)
    
    course_structure_data = models.JSONField(default=dict)

    search_vector = SearchVectorField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, auto_now_add=False)

    source_file_path = models.CharField(max_length=500, null=True, blank=True)
    research_areas = ArrayField(
        models.CharField(max_length=255), default=list, blank=True
    )
    position_type = models.CharField(max_length=100, null=True, blank=True)
    funding_type = models.CharField(max_length=50, null=True, blank=True)
    job_email = models.CharField(max_length=100, null=True, blank=True)
    job_emails = ArrayField(
        models.CharField(max_length=100, null=True, blank=True), default=list
    )
    job_telephone = models.CharField(max_length=50, null=True, blank=True)
    duration = models.IntegerField(null=True, blank=True)
    application_type = models.CharField(max_length=100, null=True, blank=True)
    application_process = models.TextField(null=True, blank=True)
    application_link = models.URLField(blank=True, null=True, max_length=500)
    job_deadline = models.DateField(null=True, blank=True)

    def __str__(self):
        university_name = self.university.name if self.university else None
        if self.type == 'JOB_POSITION':
            return f"Job Position at {university_name or 'Unknown'}"
        course_degree_name = self.courseDegree.name if self.courseDegree else None
        return f"{university_name} <> {course_degree_name}"
    
    
    

class ProgramFitmeterResult(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.CASCADE)
    program = models.ForeignKey('Program', on_delete=models.CASCADE)
    overall_score = models.FloatField(null=True, blank=True)
    dimension_scores = models.JSONField(null=True, blank=True)
    fit_level = models.CharField(max_length=50, null=True, blank=True)
    key_points = models.JSONField(null=True, blank=True)
    confidence_score = models.CharField(max_length=20, null=True, blank=True)
    raw_response = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Fitmeter: {self.user_id} - {self.program_id}"


class ProgramROIResult(models.Model):
    user = models.ForeignKey('users.User', on_delete=models.CASCADE)
    program = models.ForeignKey('Program', on_delete=models.CASCADE)
    score = models.FloatField(null=True, blank=True)
    band = models.CharField(max_length=20, null=True, blank=True)
    reasons = models.JSONField(null=True, blank=True)
    confidence = models.CharField(max_length=20, null=True, blank=True)
    raw_response = models.JSONField(null=True, blank=True)
    ten_year_npv_usd = models.FloatField(null=True, blank=True)
    payback_period_years = models.FloatField(null=True, blank=True)
    median_salary_usd = models.FloatField(null=True, blank=True)
    current_salary_usd = models.FloatField(null=True, blank=True)
    current_country = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"ROI: {self.user_id} - {self.program_id}"


class DocumentPrompt(models.Model):

    prompt = models.TextField(null=True,blank=True)
    program = models.ForeignKey(Program,on_delete=models.CASCADE)
    programEntryRequirement = models.ForeignKey(ProgramEntryRequirements,on_delete=models.SET_NULL,null=True,blank=True)
    isUserAdded = models.BooleanField(default=False)
    addedBy = models.ForeignKey('users.User',on_delete=models.SET_NULL,null=True,blank=True)
    