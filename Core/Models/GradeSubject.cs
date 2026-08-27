namespace Core.Models
{
    public class GradeSubject
    {
        public int Id { get; set; }

        public int GradeId { get; set; }
        public Grade? Grade { get; set; }

        public int SubjectId { get; set; }
        public Subject? Subject { get; set; }
    }
}
