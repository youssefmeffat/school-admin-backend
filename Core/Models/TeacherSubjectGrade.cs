namespace Core.Models
{
    public class TeacherSubjectGrade
    {
        public int Id { get; set; }

        public int TeacherId { get; set; }
        public Teacher? Teacher { get; set; }

        public int SubjectId { get; set; }
        public Subject? Subject { get; set; }

        public int GradeId { get; set; }
        public Grade? Grade { get; set; }
    }
}
