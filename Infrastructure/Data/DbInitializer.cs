using Core.Enums;
using Core.Models;
using Microsoft.EntityFrameworkCore;

namespace Infrastructure.Data
{
    public static class DbInitializer
    {
        public static async Task SeedAsync(SchoolDbContext db)
        {
            // ---------------------------------------------------------
            // School
            // ---------------------------------------------------------
            var school = await db.Schools.FirstOrDefaultAsync();

            if (school == null)
            {
                school = new School
                {
                    Name = "Al Noor School"
                };

                db.Schools.Add(school);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Grades 1-6
            // ---------------------------------------------------------
            var grades = await db.Grades
                .OrderBy(g => g.Number)
                .ToListAsync();

            if (grades.Count == 0)
            {
                grades = new List<Grade>
                {
                    new Grade
                    {
                        Name = "Grade 1",
                        Number = 1,
                        Description = "First grade"
                    },
                    new Grade
                    {
                        Name = "Grade 2",
                        Number = 2,
                        Description = "Second grade"
                    },
                    new Grade
                    {
                        Name = "Grade 3",
                        Number = 3,
                        Description = "Third grade"
                    },
                    new Grade
                    {
                        Name = "Grade 4",
                        Number = 4,
                        Description = "Fourth grade"
                    },
                    new Grade
                    {
                        Name = "Grade 5",
                        Number = 5,
                        Description = "Fifth grade"
                    },
                    new Grade
                    {
                        Name = "Grade 6",
                        Number = 6,
                        Description = "Sixth grade"
                    }
                };

                db.Grades.AddRange(grades);
                await db.SaveChangesAsync();
            }
            else
            {
                var existingNumbers = grades
                    .Select(g => g.Number)
                    .ToHashSet();

                var missingGrades = new List<Grade>();

                if (!existingNumbers.Contains(1))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 1",
                        Number = 1,
                        Description = "First grade"
                    });
                }

                if (!existingNumbers.Contains(2))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 2",
                        Number = 2,
                        Description = "Second grade"
                    });
                }

                if (!existingNumbers.Contains(3))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 3",
                        Number = 3,
                        Description = "Third grade"
                    });
                }

                if (!existingNumbers.Contains(4))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 4",
                        Number = 4,
                        Description = "Fourth grade"
                    });
                }

                if (!existingNumbers.Contains(5))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 5",
                        Number = 5,
                        Description = "Fifth grade"
                    });
                }

                if (!existingNumbers.Contains(6))
                {
                    missingGrades.Add(new Grade
                    {
                        Name = "Grade 6",
                        Number = 6,
                        Description = "Sixth grade"
                    });
                }

                if (missingGrades.Count > 0)
                {
                    db.Grades.AddRange(missingGrades);
                    await db.SaveChangesAsync();

                    grades.AddRange(missingGrades);

                    grades = grades
                        .OrderBy(g => g.Number)
                        .ToList();
                }
            }

            // ---------------------------------------------------------
            // Create 3 classes for each grade
            // ---------------------------------------------------------
            var classes = await db.Classes
                .Where(c => c.SchoolId == school.Id)
                .ToListAsync();

            if (classes.Count == 0)
            {
                classes = new List<Class>();

                foreach (var g in grades)
                {
                    for (int i = 1; i <= 3; i++)
                    {
                        classes.Add(new Class
                        {
                            Name = $"{g.Name} - Class {i}",
                            Grade = g,
                            School = school
                        });
                    }
                }

                db.Classes.AddRange(classes);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Students
            // ---------------------------------------------------------
            var studentNames = new[]
            {
                "Mohamed Ahmed",
                "Ahmed Mohamed",
                "Mohamed Ali",
                "Omar Mostafa",
                "Youssef Hassan",
                "Mariam Ibrahim",
                "Sara Mohamed",
                "Fatma Ali",
                "Hussein Mohamed",
                "Khaled Hassan",
                "Aya Mahmoud",
                "Nour Ahmed",
                "Noha Ali",
                "Amr Hassan",
                "Amina Mohamed",
                "Doaa Ahmed",
                "Heba Salah",
                "Salma Mohamed",
                "Ramy Adel",
                "Tamer Mahmoud",
                "Karim Adel",
                "Lina Samir",
                "Rana Hossam",
                "Mostafa Hamdy",
                "Mona Khaled",
                "Yara Samir",
                "Ibrahim Adel",
                "Sana Mohamed",
                "Walid Omar",
                "Nadia Fathy",
                "Dina Youssef",
                "Hany Farouk",
                "Mahmoud Salah",
                "Hoda Nasser",
                "Nabil Farid",
                "Samer Galal",
                "Rania Youssef",
                "Eman Hossam",
                "Sherif Sameh",
                "Maha Adel"
            };

            var students = await db.Students
                .Include(s => s.Class)
                .ToListAsync();

            if (students.Count == 0)
            {
                var rnd = new Random(12345);

                students = new List<Student>();

                for (int i = 0; i < studentNames.Length; i++)
                {
                    var name = studentNames[i];
                    var cls = classes[i % classes.Count];

                    students.Add(new Student
                    {
                        FullName = name,
                        Code = $"S{i + 1001}",
                        Class = cls,
                        EnrollDate =
                            DateTime.UtcNow.AddDays(
                                -rnd.Next(0, 365)
                            )
                    });
                }

                db.Students.AddRange(students);
                await db.SaveChangesAsync();

                students = await db.Students
                    .Include(s => s.Class)
                    .ToListAsync();
            }

            // ---------------------------------------------------------
            // Teachers
            // ---------------------------------------------------------
            var teacherNames = new[]
            {
                "Ali Mahmoud",
                "Heba Mostafa",
                "Khaled Gamal",
                "Mona Samir",
                "Salah Fouad",
                "Yasmin Adel"
            };

            var teachers = await db.Teachers.ToListAsync();

            if (teachers.Count == 0)
            {
                var rnd = new Random(12345);

                teachers = teacherNames
                    .Select((n, idx) => new Teacher
                    {
                        FullName = n,
                        Code = $"T{idx + 1:000}",
                        Email =
                            $"{n.Replace(' ', '.').ToLower()}@school.local",
                        HireDate =
                            DateTime.UtcNow.AddYears(
                                -rnd.Next(1, 10)
                            )
                    })
                    .ToList();

                db.Teachers.AddRange(teachers);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Subjects
            // ---------------------------------------------------------
            var subjects = await db.Subjects.ToListAsync();

            if (subjects.Count == 0)
            {
                subjects = new List<Subject>
                {
                    new Subject
                    {
                        Name = "Mathematics",
                        Code = "MATH",
                        Description = "Mathematics"
                    },
                    new Subject
                    {
                        Name = "Arabic",
                        Code = "AR",
                        Description = "Arabic Language"
                    },
                    new Subject
                    {
                        Name = "Science",
                        Code = "SCI",
                        Description = "Science"
                    },
                    new Subject
                    {
                        Name = "English",
                        Code = "ENG",
                        Description = "English"
                    }
                };

                db.Subjects.AddRange(subjects);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Grade-subject mappings
            // ---------------------------------------------------------
            var gradeSubjects =
                await db.GradeSubjects.ToListAsync();

            if (gradeSubjects.Count == 0)
            {
                gradeSubjects = new List<GradeSubject>();

                foreach (var g in grades)
                {
                    foreach (var s in subjects)
                    {
                        gradeSubjects.Add(
                            new GradeSubject
                            {
                                Grade = g,
                                Subject = s
                            }
                        );
                    }
                }

                db.GradeSubjects.AddRange(gradeSubjects);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Teacher-subject-grade assignments
            // ---------------------------------------------------------
            var teacherSubjectGrades =
                await db.TeacherSubjectGrades.ToListAsync();

            if (teacherSubjectGrades.Count == 0)
            {
                teacherSubjectGrades =
                    new List<TeacherSubjectGrade>();

                for (int i = 0; i < teachers.Count; i++)
                {
                    var t = teachers[i];
                    var s = subjects[i % subjects.Count];
                    var g = grades[i % grades.Count];

                    teacherSubjectGrades.Add(
                        new TeacherSubjectGrade
                        {
                            Teacher = t,
                            Subject = s,
                            Grade = g
                        }
                    );
                }

                db.TeacherSubjectGrades.AddRange(
                    teacherSubjectGrades
                );

                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Teaching assignments
            // ---------------------------------------------------------
            var teachingAssignments =
                await db.TeachingAssignments.ToListAsync();

            if (teachingAssignments.Count == 0)
            {
                teachingAssignments =
                    new List<TeachingAssignment>();

                for (int i = 0; i < classes.Count; i++)
                {
                    var cls = classes[i];
                    var t = teachers[i % teachers.Count];
                    var s = subjects[i % subjects.Count];

                    teachingAssignments.Add(
                        new TeachingAssignment
                        {
                            Teacher = t,
                            Subject = s,
                            Class = cls
                        }
                    );
                }

                db.TeachingAssignments.AddRange(
                    teachingAssignments
                );

                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Exams
            // ---------------------------------------------------------
            var exams = await db.Exams.ToListAsync();

            if (exams.Count == 0)
            {
                var rnd = new Random(12345);

                exams = new List<Exam>();

                var examIndex = 0;

                foreach (var g in grades)
                {
                    foreach (var s in subjects)
                    {
                        string status;
                        DateTime? examDate;

                        switch (examIndex % 3)
                        {
                            case 0:
                                status = "Unscheduled";
                                examDate = null;
                                break;

                            case 1:
                                status = "Scheduled";
                                examDate = DateTime.UtcNow.AddDays(
                                    rnd.Next(7, 60)
                                );
                                break;

                            default:
                                status = "Completed";
                                examDate = DateTime.UtcNow.AddDays(
                                    -rnd.Next(1, 60)
                                );
                                break;
                        }

                        exams.Add(
                            new Exam
                            {
                                Name = $"{s.Name} Exam",
                                Subject = s,
                                Grade = g,
                                ExamDate = examDate,
                                MaxScore = 100m,
                                Status = status
                            }
                        );

                        examIndex++;
                    }
                }

                db.Exams.AddRange(exams);
                await db.SaveChangesAsync();
            }

            // ---------------------------------------------------------
            // Student exam results
            // ---------------------------------------------------------
            if (!await db.StudentExamResults.AnyAsync())
            {
                var rnd = new Random(12345);

                var results =
                    new List<StudentExamResult>();

                foreach (var ex in exams)
                {
                    var gradeStudents = students
                        .Where(s =>
                            s.Class != null &&
                            s.Class.GradeId == ex.Grade.Id
                        )
                        .Take(8)
                        .ToList();

                    foreach (var st in gradeStudents)
                    {
                        results.Add(
                            new StudentExamResult
                            {
                                StudentId = st.Id,
                                ExamId = ex.Id,
                                Score = rnd.Next(50, 101),
                                Notes = "Seeded result"
                            }
                        );
                    }
                }

                db.StudentExamResults.AddRange(results);
                await db.SaveChangesAsync();
            }

            // =========================================================
            // ATTENDANCE RESET + SEED
            // =========================================================
            //
            // IMPORTANT:
            // Every time the initializer runs, ALL attendance for the
            // current month is removed and regenerated.
            //
            // This intentionally removes the old Friday/Saturday
            // records that were generated by the previous version.
            //
            // Weekend:
            //     Friday
            //     Saturday
            //
            // School days:
            //     Sunday
            //     Monday
            //     Tuesday
            //     Wednesday
            //     Thursday
            //
            // =========================================================

            var today = DateTime.UtcNow.Date;

            var firstDayOfMonth =
                new DateTime(
                    today.Year,
                    today.Month,
                    1
                );

            var firstDayOfNextMonth =
                firstDayOfMonth.AddMonths(1);

            // ---------------------------------------------------------
            // DELETE ALL CURRENT-MONTH STUDENT ATTENDANCE
            // ---------------------------------------------------------

            var currentMonthStudentAttendance =
                await db.StudentAttendances
                    .Where(a =>
                        a.Date >= firstDayOfMonth &&
                        a.Date < firstDayOfNextMonth
                    )
                    .ToListAsync();

            if (currentMonthStudentAttendance.Count > 0)
            {
                db.StudentAttendances.RemoveRange(
                    currentMonthStudentAttendance
                );
            }

            // ---------------------------------------------------------
            // DELETE ALL CURRENT-MONTH TEACHER ATTENDANCE
            // ---------------------------------------------------------

            var currentMonthTeacherAttendance =
                await db.TeacherAttendances
                    .Where(a =>
                        a.Date >= firstDayOfMonth &&
                        a.Date < firstDayOfNextMonth
                    )
                    .ToListAsync();

            if (currentMonthTeacherAttendance.Count > 0)
            {
                db.TeacherAttendances.RemoveRange(
                    currentMonthTeacherAttendance
                );
            }

            // Commit the deletion first because the attendance tables
            // enforce unique person/date indexes.
            await db.SaveChangesAsync();

            // ---------------------------------------------------------
            // Build school days
            // ---------------------------------------------------------

            var schoolDays =
                new List<DateTime>();

            for (
                var date = firstDayOfMonth;
                date <= today;
                date = date.AddDays(1)
            )
            {
                // Friday and Saturday are weekends.
                if (
                    date.DayOfWeek == DayOfWeek.Friday ||
                    date.DayOfWeek == DayOfWeek.Saturday
                )
                {
                    continue;
                }

                schoolDays.Add(date);
            }

            // ---------------------------------------------------------
            // Deterministic random generator
            // ---------------------------------------------------------

            var attendanceRandom =
                new Random(20260820);

            var studentAttendanceToAdd =
                new List<StudentAttendance>();

            var teacherAttendanceToAdd =
                new List<TeacherAttendance>();

            // =========================================================
            // STUDENT ATTENDANCE
            // =========================================================

            foreach (var date in schoolDays)
            {
                foreach (var student in students)
                {
                    var roll =
                        attendanceRandom.NextDouble();

                    AttendanceStatus status;

                    if (roll < 0.88)
                    {
                        status =
                            AttendanceStatus.Present;
                    }
                    else if (roll < 0.94)
                    {
                        status =
                            AttendanceStatus.Late;
                    }
                    else if (roll < 0.985)
                    {
                        status =
                            AttendanceStatus.Absent;
                    }
                    else
                    {
                        status =
                            AttendanceStatus.Excused;
                    }

                    TimeSpan? checkInTime = null;
                    TimeSpan? checkOutTime = null;

                    if (
                        status ==
                            AttendanceStatus.Present ||
                        status ==
                            AttendanceStatus.Late
                    )
                    {
                        var checkInMinutes =
                            status ==
                                AttendanceStatus.Late
                                    ? 8 * 60 +
                                      attendanceRandom.Next(
                                          10,
                                          46
                                      )
                                    : 7 * 60 +
                                      attendanceRandom.Next(
                                          20,
                                          51
                                      );

                        checkInTime =
                            TimeSpan.FromMinutes(
                                checkInMinutes
                            );

                        checkOutTime =
                            TimeSpan.FromMinutes(
                                14 * 60 +
                                attendanceRandom.Next(
                                    0,
                                    31
                                )
                            );
                    }

                    studentAttendanceToAdd.Add(
                        new StudentAttendance
                        {
                            StudentId = student.Id,
                            Date = date,
                            Status = status,
                            CheckInTime = checkInTime,
                            CheckOutTime = checkOutTime,
                            Notes = "Seeded attendance",
                            RecordedBy = null,
                            CreatedAt = date.AddHours(15),
                            UpdatedAt = date.AddHours(15)
                        }
                    );
                }
            }

            // =========================================================
            // TEACHER ATTENDANCE
            // =========================================================

            foreach (var date in schoolDays)
            {
                foreach (var teacher in teachers)
                {
                    var roll =
                        attendanceRandom.NextDouble();

                    AttendanceStatus status;

                    if (roll < 0.94)
                    {
                        status =
                            AttendanceStatus.Present;
                    }
                    else if (roll < 0.975)
                    {
                        status =
                            AttendanceStatus.Late;
                    }
                    else if (roll < 0.995)
                    {
                        status =
                            AttendanceStatus.Absent;
                    }
                    else
                    {
                        status =
                            AttendanceStatus.Excused;
                    }

                    TimeSpan? checkInTime = null;
                    TimeSpan? checkOutTime = null;

                    if (
                        status ==
                            AttendanceStatus.Present ||
                        status ==
                            AttendanceStatus.Late
                    )
                    {
                        var checkInMinutes =
                            status ==
                                AttendanceStatus.Late
                                    ? 7 * 60 +
                                      attendanceRandom.Next(
                                          35,
                                          61
                                      )
                                    : 7 * 60 +
                                      attendanceRandom.Next(
                                          0,
                                          31
                                      );

                        checkInTime =
                            TimeSpan.FromMinutes(
                                checkInMinutes
                            );

                        checkOutTime =
                            TimeSpan.FromMinutes(
                                15 * 60 +
                                attendanceRandom.Next(
                                    0,
                                    61
                                )
                            );
                    }

                    teacherAttendanceToAdd.Add(
                        new TeacherAttendance
                        {
                            TeacherId = teacher.Id,
                            Date = date,
                            Status = status,
                            CheckInTime = checkInTime,
                            CheckOutTime = checkOutTime,
                            Notes = "Seeded attendance",
                            RecordedBy = null,
                            CreatedAt = date.AddHours(16),
                            UpdatedAt = date.AddHours(16)
                        }
                    );
                }
            }

            // ---------------------------------------------------------
            // Save regenerated attendance
            // ---------------------------------------------------------

            if (studentAttendanceToAdd.Count > 0)
            {
                db.StudentAttendances.AddRange(
                    studentAttendanceToAdd
                );
            }

            if (teacherAttendanceToAdd.Count > 0)
            {
                db.TeacherAttendances.AddRange(
                    teacherAttendanceToAdd
                );
            }

            if (
                studentAttendanceToAdd.Count > 0 ||
                teacherAttendanceToAdd.Count > 0
            )
            {
                await db.SaveChangesAsync();
            }
        }
    }
}
