from django.db import models
from django.contrib.postgres.fields import ArrayField
from programs.models import (University,StudentDiversity,ScholarshipProvider,)
from django.contrib.postgres.search import SearchVectorField

class CollegeTypeChoices(models.TextChoices):

    GRAD_SCHOOL = 'GRAD_SCHOOL'
    UG_COLLEGE = 'UG_COLLEGE'


class ApplicationRequirementChoices(models.TextChoices):
    CLASS_9_SCORE = 'CLASS_9_SCORE', 'Class 9 score'
    PERSONAL_STATEMENT_SOP = 'PERSONAL_STATEMENT_SOP', 'Personal Statement / SOP'
    ENGLISH_PROFICIENCY_IELTS = 'ENGLISH_PROFICIENCY_IELTS', 'English proficiency (IELTS)'
    LOR = 'LOR', 'LOR'
    SAT_ACT = 'SAT_ACT', 'SAT / ACT'
    APPLICATION_PORTAL = 'APPLICATION_PORTAL', 'Application portal'
    APPLICATION_FEE = 'APPLICATION_FEE', 'Application fee'



class CollegeMajor(models.Model):

    name = models.CharField(max_length=255,null=True,blank=True)
    isActive = models.BooleanField(default=True)
    parentMajor = models.ForeignKey('self',on_delete=models.SET_NULL, null=True, blank=True, related_name='subMajors')

class College(models.Model):

    name = models.CharField(max_length=255,null=True,blank=True)
    university = models.ForeignKey(University,on_delete=models.CASCADE)
    address = models.CharField(max_length=255, blank=True, null=True)
    fullAddress = models.CharField(max_length=255,null=True,blank=True)
    phoneNumber = models.CharField(max_length=255,null=True,blank=True)
    schoolType = models.CharField(max_length=255,null=True,blank=True)
    acceptanceRate = models.FloatField(null=True,blank=True)
    yieldRate = models.FloatField(null=True,blank=True)
    majors = models.ManyToManyField(CollegeMajor,blank=True)
    isVerified = models.BooleanField(default=False)
    isActive = models.BooleanField(default=True)
    collegeType = models.CharField(max_length=255,choices=CollegeTypeChoices.choices,default=CollegeTypeChoices.UG_COLLEGE)
    search_vector = SearchVectorField(null=True)
    overview_description = models.TextField(null=True,blank=True)
    view_priority = models.IntegerField(default=10)
    document_requirements = ArrayField(models.TextField(), default=list,null=True)
    application_fee = models.FloatField(null=True,blank=True)
    tuition_fee_per_year = models.FloatField(null=True,blank=True)
    total_tuition_fee_per_year = models.FloatField(null=True,blank=True)
    health_insurance_cost_per_year = models.FloatField(null=True,blank=True)
    tuition_fee = models.FloatField(null=True,blank=True)
    total_tuition_fee = models.FloatField(null=True,blank=True)
    application_fee_page_link = models.URLField(max_length=500,null=True,blank=True)
    additional_info_page_link = models.URLField(max_length=500,null=True,blank=True)
    recruiters = models.ManyToManyField('programs.Company', related_name='collegeRecruiters', blank=True)
    career_outcome_description = models.TextField(null=True,blank=True)
    avg_earning_per_year = models.FloatField(null=True,blank=True)
    job_placement_rate = models.FloatField(null=True,blank=True)
    graduation_rate = models.FloatField(null=True,blank=True)
    job_roles = models.ManyToManyField('programs.JobRole', related_name='collegeJobRoles', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, auto_now_add=False)


class CollegeApplicationDeadline(models.Model):
    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name='application_deadlines')
    deadline_type = models.CharField(max_length=255, null=True, blank=True)
    deadline_date = models.DateField(null=True, blank=True)
    
    class Meta:
        unique_together = ['college', 'deadline_type']

class ApplicationRequirements(models.Model):
    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name='application_requirements')
    requirement = models.CharField(
        max_length=255,
        choices=ApplicationRequirementChoices.choices,
        null=True,
        blank=True,
    )
    requirement_detail = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = ['college', 'requirement']
        
    
################# Invalid Model Now #######################
class CollegeMetaData(models.Model):

    college = models.OneToOneField(College,on_delete=models.CASCADE)
    totalYears = models.FloatField(null=True,blank=True)
    acceptanceRateWomen = models.FloatField(null=True,blank=True)
    acceptanceRateMen = models.FloatField(null=True,blank=True)
    totalApplicants = models.IntegerField(null=True,blank=True)
    percentageWomenApplicants = models.FloatField(null=True,blank=True)
    percentageMenApplicants = models.FloatField(null=True,blank=True)
    admissionWebsite = models.CharField(max_length=255,null=True,blank=True)
    fulltimeEnrollments = models.IntegerField(blank=True, null=True)

    currency = models.CharField(max_length=25, default='$', null=True, blank=True)
    totalCost = models.FloatField(null=True,blank=True)
    inStateCost = models.FloatField(null=True,blank=True)
    outOfStateCost = models.CharField(max_length=255,null=True,blank=True)
    medianSalary = models.CharField(max_length=255,null=True,blank=True)
    livingCost = models.CharField(max_length=255,null=True,blank=True)
    inStateTuitionFees = models.FloatField(null=True,blank=True)
    inStateFees = models.FloatField(null=True,blank=True)
    outOfStateTuition = models.FloatField(null=True,blank=True)
    outOfStateFees = models.FloatField(null=True,blank=True)
    roomAndBoardFees = models.FloatField(null=True,blank=True)
    pellGrants = models.FloatField(null=True,blank=True)

    percentGraduatesAwardedLoans = models.FloatField(null=True,blank=True)
    avgAmountAwarded = models.FloatField(null=True,blank=True)
    fourYearGradRate = models.FloatField(null=True,blank=True)
    sixYearGradRate = models.FloatField(null=True,blank=True)
    firstyearEnrolledStudents = models.IntegerField(null=True,blank=True) 
    studentDiversityType = models.CharField(max_length=255,null=True,blank=True)
    retentionRate = models.FloatField(null=True,blank=True)
    graduationRate = models.FloatField(null=True,blank=True)
    jobPlacementRate = models.FloatField(null=True,blank=True)
    admissionPolicy = models.CharField(max_length=255,null=True,blank=True)
    internationalStudents = models.FloatField(null=True,blank=True)
    applicationDeadline = models.DateField(null=True,blank=True)
    womenEnrolled = models.FloatField(null=True,blank=True)
    menEnrolled = models.FloatField(null=True,blank=True)
    studentFacultyRatio = models.CharField(max_length=255,null=True,blank=True)
    calendarSystem = models.CharField(max_length=255,null=True,blank=True)
    labels = ArrayField(models.CharField(max_length=255), default=list,null=True)
    specialAcademicOfferings = ArrayField(models.CharField(max_length=255), default=list,null=True)
    isActive = models.BooleanField(default=True)
    applicationEntryRequirements = ArrayField(models.CharField(max_length=255), default=list,null=True)
    percentSATSubmitted = models.FloatField(null=True,blank=True)
    percentACTSubmitted = models.FloatField(null=True,blank=True)
    currencySymbol = models.CharField(blank=True,null=True)

    totalGraduateStudents = models.FloatField(null=True,blank=True)
    partTimeGraduateStudents = models.FloatField(null=True,blank=True)
    researchAssistants = models.IntegerField(null=True,blank=True)
    teachingAssistants = models.IntegerField(null=True,blank=True)

    overviewDescription = models.TextField(null=True,blank=True)
    admissionsDescription = models.TextField(null=True,blank=True)
    costDescription = models.TextField(null=True,blank=True)
    applicationRequirementsDescription = models.TextField(null=True,blank=True)
    academicsDescription = models.TextField(null=True,blank=True)
    studentsDescription = models.TextField(null=True,blank=True)
    fundingDescription = models.TextField(null=True,blank=True)
    afterCollegeDescription = models.TextField(null=True,blank=True)
    graduateStudentsDescription = models.TextField(null=True,blank=True)

    avgSATScore = models.CharField(max_length=255,null=True,blank=True)
    avgACTScore = models.CharField(max_length=255,null=True,blank=True)
    actMathScoreRange = models.CharField(max_length=255,null=True,blank=True)
    actReadingWritingRange = models.CharField(max_length=255,null=True,blank=True)
    satRange = models.CharField(max_length=255,null=True,blank=True)
    actRange = models.CharField(max_length=255,null=True,blank=True)
    studentsSubmittingSAT = models.FloatField(null=True,blank=True)
    satMathScoreRange = models.CharField(max_length=255,null=True,blank=True)
    satReadingWritingRange = models.CharField(max_length=255,null=True,blank=True)

    scholarshipProviders = models.ManyToManyField(ScholarshipProvider, blank=True)
    fundingOptionsTags = ArrayField(models.CharField(max_length=255), default=list)

    scholarshipDescription = models.TextField(null=True,blank=True)


class UGDocumentPromptTypes(models.TextChoices):
    
    COMMON_APP = 'COMMON_APP'
    COALITION  = 'COALITION'
    COLLEGE = 'COLLEGE'


class UGDocumentPrompt(models.Model):

    prompt = models.TextField(null=True,blank=True)
    type = models.CharField(choices=UGDocumentPromptTypes.choices,default=UGDocumentPromptTypes.COMMON_APP)
    year = models.CharField(null=True,blank=True)
    isActive = models.BooleanField(default=True)
    isUserAdded = models.BooleanField(default=False)
    addedBy = models.ForeignKey('users.User',on_delete=models.SET_NULL,null=True,blank=True)