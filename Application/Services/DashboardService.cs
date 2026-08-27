using Infrastructure.Data;
using Microsoft.EntityFrameworkCore;

namespace Application.Services
{
    public class DashboardService : IDashboardService
    {
        private readonly SchoolDbContext _db;

        public DashboardService(SchoolDbContext db)
        {
            _db = db;
        }

        public async Task<object> GetStatsAsync()
        {
            var today = DateTime.UtcNow.Date;
            var sevenDaysFromNow = today.AddDays(7);

            var firstDayOfMonth = new DateTime(
                today.Year,
                today.Month,
                1
            );

            var firstDayOfNextMonth =
                firstDayOfMonth.AddMonths(1);

            // =====================================================
            // TOP STAT CARDS
            // =====================================================

            var totalStudents =
                await _db.Students.CountAsync();

            var totalTeachers =
                await _db.Teachers.CountAsync();

            var totalClasses =
                await _db.Classes.CountAsync();

            var examsThisWeek =
                await _db.Exams.CountAsync(e =>
                    e.ExamDate.HasValue &&
                    e.ExamDate.Value.Date >= today &&
                    e.ExamDate.Value.Date < sevenDaysFromNow
                );

            var totalSubjects =
                await _db.Subjects.CountAsync();


            // =====================================================
            // ENROLLMENT BY LEVEL
            // =====================================================

            var enrollmentByLevel =
                await _db.Grades
                    .OrderBy(g => g.Number)
                    .Select(g => new
                    {
                        id = g.Id,
                        name = g.Name,
                        number = g.Number,

                        studentCount =
                            _db.Students.Count(s =>
                                s.ClassId != null &&
                                _db.Classes.Any(c =>
                                    c.Id == s.ClassId &&
                                    c.GradeId == g.Id
                                )
                            )
                    })
                    .ToListAsync();


            // =====================================================
            // STUDENTS PER CLASS
            // =====================================================

            var studentsPerClass =
                await _db.Classes
                    .OrderBy(c => c.Grade.Number)
                    .ThenBy(c => c.Name)
                    .Select(c => new
                    {
                        id = c.Id,
                        name = c.Name,
                        gradeId = c.GradeId,
                        gradeName = c.Grade.Name,

                        studentCount =
                            _db.Students.Count(s =>
                                s.ClassId == c.Id
                            )
                    })
                    .ToListAsync();


            // =====================================================
            // GRADE SUBJECT RELATIONSHIPS
            // =====================================================

            var gradeSubjects =
                await _db.GradeSubjects
                    .Select(gs => new
                    {
                        gradeId = gs.GradeId,
                        subjectId = gs.SubjectId,

                        subjectName =
                            gs.Subject != null
                                ? gs.Subject.Name
                                : "Unknown subject",

                        gradeName =
                            gs.Grade != null
                                ? gs.Grade.Name
                                : "Unknown grade"
                    })
                    .ToListAsync();


            // =====================================================
            // STUDENT EXAM SCORES
            // =====================================================

            var examScores =
                await _db.StudentExamResults
                    .Where(r =>
                        r.Score.HasValue &&
                        r.Exam != null &&
                        r.Exam.MaxScore > 0
                    )
                    .Select(r => new
                    {
                        gradeId = r.Exam!.GradeId,
                        subjectId = r.Exam.SubjectId,
                        score = r.Score!.Value,
                        maxScore = r.Exam.MaxScore
                    })
                    .ToListAsync();


            // =====================================================
            // AVERAGE SCORE BY GRADE + SUBJECT
            // =====================================================

            var scoreAverages =
                examScores
                    .GroupBy(x => new
                    {
                        x.gradeId,
                        x.subjectId
                    })
                    .Select(g => new
                    {
                        gradeId = g.Key.gradeId,
                        subjectId = g.Key.subjectId,

                        averageScore =
                            g.Average(x =>
                                (x.score / x.maxScore) * 100
                            )
                    })
                    .ToList();


            var averageScoreBySubject =
                gradeSubjects
                    .Select(gs =>
                    {
                        var average =
                            scoreAverages.FirstOrDefault(a =>
                                a.gradeId == gs.gradeId &&
                                a.subjectId == gs.subjectId
                            );

                        return new
                        {
                            gradeId = gs.gradeId,
                            subjectId = gs.subjectId,
                            subjectName = gs.subjectName,
                            gradeName = gs.gradeName,

                            averageScore =
                                average?.averageScore
                        };
                    })
                    .OrderBy(x => x.gradeId)
                    .ThenBy(x => x.subjectName)
                    .ToList();


            // =====================================================
            // UPCOMING EXAMS
            // =====================================================

            var upcomingExams =
                await _db.Exams
                    .Where(e =>
                        e.ExamDate.HasValue &&
                        e.ExamDate.Value.Date >= today
                    )
                    .OrderBy(e => e.ExamDate)
                    .Take(3)
                    .Select(e => new
                    {
                        id = e.Id,

                        title = e.Name,

                        subjectName =
                            e.Subject != null
                                ? e.Subject.Name
                                : "Unknown subject",

                        gradeName =
                            e.Grade != null
                                ? e.Grade.Name
                                : "Unknown grade",

                        examDate = e.ExamDate
                    })
                    .ToListAsync();


            // =====================================================
            // TEACHERS
            // =====================================================

            var teacherAssignments =
                await (
                    from ta in _db.TeachingAssignments

                    join teacher in _db.Teachers
                        on ta.TeacherId equals teacher.Id

                    join subject in _db.Subjects
                        on ta.SubjectId equals subject.Id

                    join cls in _db.Classes
                        on ta.ClassId equals cls.Id

                    join grade in _db.Grades
                        on cls.GradeId equals grade.Id

                    select new
                    {
                        teacherId = teacher.Id,
                        teacherName = teacher.FullName,

                        classId = cls.Id,
                        className = cls.Name,

                        gradeId = grade.Id,
                        gradeName = grade.Name,
                        gradeNumber = grade.Number,

                        subjectId = subject.Id,
                        subjectName = subject.Name
                    }
                )
                .ToListAsync();


            var teachers =
                teacherAssignments
                    .GroupBy(x => new
                    {
                        x.teacherId,
                        x.teacherName
                    })
                    .Select(g => new
                    {
                        id = g.Key.teacherId,
                        name = g.Key.teacherName,

                        classCount =
                            g.Select(x => x.classId)
                                .Distinct()
                                .Count(),

                        classes =
                            g.GroupBy(x => new
                            {
                                x.classId,
                                x.className,
                                x.gradeId,
                                x.gradeName,
                                x.gradeNumber
                            })
                            .OrderBy(x => x.Key.gradeNumber)
                            .ThenBy(x => x.Key.className)
                            .Select(c => new
                            {
                                id = c.Key.classId,
                                name = c.Key.className,
                                gradeId = c.Key.gradeId,
                                gradeName = c.Key.gradeName,
                                gradeNumber = c.Key.gradeNumber,

                                subjects =
                                    c.Select(x => new
                                    {
                                        id = x.subjectId,
                                        name = x.subjectName
                                    })
                                    .Distinct()
                                    .OrderBy(x => x.name)
                                    .ToList()
                            })
                            .ToList()
                    })
                    .OrderBy(x => x.name)
                    .ToList();


            // =====================================================
            // ATTENDANCE - CURRENT MONTH
            // =====================================================
            //
            // StudentAttendance and TeacherAttendance both use:
            //
            // Date   -> attendance date
            // Status -> Present / Absent/etc.
            //
            // Student attendance percentage uses the ACTUAL
            // total number of students in the Students table.
            //
            // This is important because the number of attendance
            // records for a day may be lower than the total number
            // of students.
            //
            // Example:
            //
            // 500 students in Students table
            // 430 marked Present
            // 20 marked Absent
            //
            // studentTotal = 500
            // studentPresent = 430
            // studentPercentage = 86%
            //
            // Missing attendance records remain part of the
            // denominator rather than being silently excluded.
            //
            // =====================================================

            var studentAttendance =
                await _db.StudentAttendances
                    .Where(a =>
                        a.Date >= firstDayOfMonth &&
                        a.Date < firstDayOfNextMonth
                    )
                    .Select(a => new
                    {
                        date = a.Date.Date,
                        status = a.Status
                    })
                    .ToListAsync();


            var teacherAttendance =
                await _db.TeacherAttendances
                    .Where(a =>
                        a.Date >= firstDayOfMonth &&
                        a.Date < firstDayOfNextMonth
                    )
                    .Select(a => new
                    {
                        date = a.Date.Date,
                        status = a.Status
                    })
                    .ToListAsync();


            var daysInMonth =
                DateTime.DaysInMonth(
                    today.Year,
                    today.Month
                );


            // =====================================================
            // ATTENDANCE HEATMAP
            // =====================================================

            var attendanceHeatmap =
                Enumerable
                    .Range(1, daysInMonth)
                    .Select(dayNumber =>
                    {
                        var date =
                            new DateTime(
                                today.Year,
                                today.Month,
                                dayNumber
                            );

                        var studentRecords =
                            studentAttendance
                                .Where(x =>
                                    x.date == date
                                )
                                .ToList();

                        var teacherRecords =
                            teacherAttendance
                                .Where(x =>
                                    x.date == date
                                )
                                .ToList();


                        // -------------------------------------------------
                        // STUDENTS
                        // -------------------------------------------------
                        //
                        // IMPORTANT:
                        // Do NOT use studentRecords.Count here.
                        //
                        // studentRecords.Count only tells us how many
                        // attendance records exist for this date.
                        //
                        // The denominator must be the actual number of
                        // students in the Students table.
                        // -------------------------------------------------

                        var studentTotal =
                            totalStudents;

                        var studentPresent =
                            studentRecords.Count(x =>
                                x.status ==
                                Core.Enums.AttendanceStatus.Present
                            );


                        // -------------------------------------------------
                        // TEACHERS
                        // -------------------------------------------------

                        var teacherTotal =
                            teacherRecords.Count;

                        var teacherPresent =
                            teacherRecords.Count(x =>
                                x.status ==
                                Core.Enums.AttendanceStatus.Present
                            );


                        return new
                        {
                            date =
                                date.ToString(
                                    "yyyy-MM-dd"
                                ),

                            day = dayNumber,

                            isFuture =
                                date > today,

                            studentPresent,
                            studentTotal,

                            studentPercentage =
                                studentTotal > 0
                                    ? (
                                        (double)studentPresent /
                                        studentTotal
                                      ) * 100
                                    : 0,

                            teacherPresent,
                            teacherTotal,

                            teacherPercentage =
                                teacherTotal > 0
                                    ? (
                                        (double)teacherPresent /
                                        teacherTotal
                                      ) * 100
                                    : 0
                        };
                    })
                    .ToList();


            // =====================================================
            // RESPONSE
            // =====================================================

            return new
            {
                totalStudents,
                totalTeachers,
                totalClasses,
                examsThisWeek,
                totalSubjects,

                enrollmentByLevel,

                studentsPerClass,

                averageScoreBySubject,

                upcomingExams,

                teachers,

                attendanceHeatmap
            };
        }
    }
}
