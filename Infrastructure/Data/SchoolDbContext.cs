using Core.Models;
using Microsoft.EntityFrameworkCore;

namespace Infrastructure.Data
{
    public class SchoolDbContext : DbContext
    {
        public SchoolDbContext(DbContextOptions<SchoolDbContext> options)
            : base(options)
        {
        }

        public DbSet<Student> Students { get; set; } = null!;
        public DbSet<Class> Classes { get; set; } = null!;
        public DbSet<School> Schools { get; set; } = null!;
        public DbSet<Teacher> Teachers { get; set; } = null!;
        public DbSet<Subject> Subjects { get; set; } = null!;
        public DbSet<Grade> Grades { get; set; } = null!;
        public DbSet<GradeSubject> GradeSubjects { get; set; } = null!;
        public DbSet<TeacherSubjectGrade> TeacherSubjectGrades { get; set; } = null!;
        public DbSet<TeachingAssignment> TeachingAssignments { get; set; } = null!;
        public DbSet<Exam> Exams { get; set; } = null!;
        public DbSet<StudentExamResult> StudentExamResults { get; set; } = null!;

        public DbSet<StudentAttendance> StudentAttendances { get; set; } = null!;
        public DbSet<TeacherAttendance> TeacherAttendances { get; set; } = null!;

        protected override void OnModelCreating(ModelBuilder modelBuilder)
        {
            // =====================================================
            // CLASS -> STUDENTS
            // =====================================================

            modelBuilder.Entity<Class>()
                .HasMany(c => c.Students)
                .WithOne(s => s.Class)
                .HasForeignKey(s => s.ClassId)
                .OnDelete(DeleteBehavior.SetNull);


            // =====================================================
            // SCHOOL -> CLASSES
            // =====================================================

            modelBuilder.Entity<School>()
                .HasMany(s => s.Classes)
                .WithOne(c => c.School)
                .HasForeignKey(c => c.SchoolId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // GRADE -> CLASSES
            // =====================================================

            modelBuilder.Entity<Grade>()
                .HasMany(g => g.Classes)
                .WithOne(c => c.Grade)
                .HasForeignKey(c => c.GradeId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // GRADE SUBJECT
            // =====================================================

            modelBuilder.Entity<GradeSubject>()
                .HasOne(gs => gs.Grade)
                .WithMany(g => g.GradeSubjects)
                .HasForeignKey(gs => gs.GradeId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<GradeSubject>()
                .HasOne(gs => gs.Subject)
                .WithMany(s => s.GradeSubjects)
                .HasForeignKey(gs => gs.SubjectId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // TEACHER SUBJECT GRADE
            // =====================================================

            modelBuilder.Entity<TeacherSubjectGrade>()
                .HasOne(tsg => tsg.Teacher)
                .WithMany(t => t.TeacherSubjectGrades)
                .HasForeignKey(tsg => tsg.TeacherId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<TeacherSubjectGrade>()
                .HasOne(tsg => tsg.Subject)
                .WithMany(s => s.TeacherSubjectGrades)
                .HasForeignKey(tsg => tsg.SubjectId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<TeacherSubjectGrade>()
                .HasOne(tsg => tsg.Grade)
                .WithMany(g => g.TeacherSubjectGrades)
                .HasForeignKey(tsg => tsg.GradeId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // TEACHING ASSIGNMENT
            // =====================================================

            modelBuilder.Entity<TeachingAssignment>()
                .HasOne(ta => ta.Teacher)
                .WithMany(t => t.TeachingAssignments)
                .HasForeignKey(ta => ta.TeacherId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<TeachingAssignment>()
                .HasOne(ta => ta.Subject)
                .WithMany()
                .HasForeignKey(ta => ta.SubjectId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<TeachingAssignment>()
                .HasOne(ta => ta.Class)
                .WithMany(c => c.TeachingAssignments)
                .HasForeignKey(ta => ta.ClassId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // EXAM
            // =====================================================

            modelBuilder.Entity<Exam>()
                .HasOne(e => e.Subject)
                .WithMany(s => s.Exams)
                .HasForeignKey(e => e.SubjectId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<Exam>()
                .HasOne(e => e.Grade)
                .WithMany()
                .HasForeignKey(e => e.GradeId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // STUDENT EXAM RESULT
            // =====================================================

            modelBuilder.Entity<StudentExamResult>()
                .HasOne(r => r.Student)
                .WithMany(s => s.StudentExamResults)
                .HasForeignKey(r => r.StudentId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<StudentExamResult>()
                .HasOne(r => r.Exam)
                .WithMany(e => e.StudentExamResults)
                .HasForeignKey(r => r.ExamId)
                .OnDelete(DeleteBehavior.Cascade);


            // =====================================================
            // STUDENT ATTENDANCE
            // =====================================================

            modelBuilder.Entity<StudentAttendance>()
                .HasOne(a => a.Student)
                .WithMany()
                .HasForeignKey(a => a.StudentId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<StudentAttendance>()
                .HasIndex(a => new
                {
                    a.StudentId,
                    a.Date
                })
                .IsUnique();


            // =====================================================
            // TEACHER ATTENDANCE
            // =====================================================

            modelBuilder.Entity<TeacherAttendance>()
                .HasOne(a => a.Teacher)
                .WithMany()
                .HasForeignKey(a => a.TeacherId)
                .OnDelete(DeleteBehavior.Cascade);

            modelBuilder.Entity<TeacherAttendance>()
                .HasIndex(a => new
                {
                    a.TeacherId,
                    a.Date
                })
                .IsUnique();
        }
    }
}